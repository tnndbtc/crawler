"""
Translation selection algorithm for selective hotness-based translation.

Selects the top X% hottest items per language group for translation,
based on admin-configurable settings.

Key Functions:
- select_items_for_translation(): Main selection algorithm
- get_translation_settings(): Get current translation settings from SystemSettings

Selection Strategy:
1. Partition items by lang_group
2. Filter by source languages (e.g., only 'en' and 'ja')
3. Exclude items with existing translations (idempotency)
4. Sort by hotness DESC
5. Select top X% using admin-configurable percentage
6. Apply small bucket rules for groups with <20 items

Settings (from SystemSettings model):
- translation_hot_percent: Top X% to translate (default: 10)
- translation_target_locales: Target locales (default: ['zh-Hans'])
- translation_source_langs: Source languages (default: ['en', 'ja'])

Usage:
    from shared.translation_selection import select_items_for_translation

    # Get items to translate to zh-Hans
    items = select_items_for_translation(target_locale='zh-Hans', limit=100)

    for item in items:
        # Translate item...
        pass
"""

import logging
from typing import List, Dict, Any
from django.db.models import Q, Exists, OuterRef
from shared.language_detection import locale_to_lang_group

logger = logging.getLogger(__name__)


def get_translation_settings() -> Dict[str, Any]:
    """
    Get translation settings from SystemSettings model.

    Returns:
        Dict with translation settings:
        {
            'hot_percent': 10,
            'target_locales': ['zh-Hans'],
            'source_langs': ['en', 'ja']
        }

    Note:
        - Imports SystemSettings lazily to avoid circular imports
        - Uses sensible defaults if settings not found
    """
    # Import here to avoid circular dependency
    from crawler_admin.models import SystemSettings

    global_pct = SystemSettings.get_setting('translation_hot_percent', default=10)
    # Explicit list of all supported lang_groups — matches the 15 locales in the plan.
    # Add new entries here when new locales are added.
    KNOWN_LANG_GROUPS = ['zh', 'ja', 'ko', 'en', 'es', 'pt', 'fr', 'de',
                         'ar', 'hi', 'id', 'ru', 'tr', 'it', 'pl']
    locale_pcts = {}
    for lang in KNOWN_LANG_GROUPS:
        key = f'translation_hot_percent_{lang}'
        locale_pcts[lang] = SystemSettings.get_setting(key, default=global_pct)

    return {
        'hot_percent': global_pct,           # backward compat — global fallback
        'hot_percent_by_locale': locale_pcts, # per-locale percentages
        'target_locales': SystemSettings.get_setting('translation_target_locales', default=['zh-Hans']),
        'source_langs': SystemSettings.get_setting('translation_source_langs', default=['en', 'ja']),
    }


def get_hot_percent_for_locale(target_locale: str, settings: Dict[str, Any]) -> int:
    """
    Get the hot_percent to use for a specific target locale.

    Looks up per-locale setting first, falls back to global.

    Args:
        target_locale: Target locale (e.g. 'zh-Hans', 'en-US', 'pt-BR')
        settings: Dict returned by get_translation_settings()

    Returns:
        Integer percentage (e.g. 25 for 25%)
    """
    lang = locale_to_lang_group(target_locale)  # 'zh-Hans' → 'zh', 'pt-BR' → 'pt'
    by_locale = settings.get('hot_percent_by_locale', {})
    return by_locale.get(lang, settings.get('hot_percent', 10))


def calculate_translation_count(total_count: int, hot_percent: int) -> int:
    """
    Calculate how many items to translate from a language group.

    Uses percentage (top X%), always at least 1 if items exist.

    Args:
        total_count: Total items in the language group
        hot_percent: Percentage to select (e.g., 10 for top 10%)

    Returns:
        Number of items to select

    Examples:
        >>> calculate_translation_count(5, 10)
        1  # 10% of 5 = 0.5 → at least 1

        >>> calculate_translation_count(100, 10)
        10  # 10% of 100 = 10

        >>> calculate_translation_count(0, 10)
        0  # Nothing to translate
    """
    if total_count == 0:
        return 0

    return max(1, int(total_count * hot_percent / 100.0))


def select_items_for_translation(target_locale: str, limit: int = 100) -> List:
    """
    Select items for translation using hotness-based selection algorithm.

    This is the main entry point for selective translation.

    Args:
        target_locale: Target locale for translation (e.g., 'zh-Hans')
        limit: Maximum total items to return (across all language groups)

    Returns:
        List of TrendItem instances to translate, sorted by hotness DESC

    Algorithm:
        1. Get translation settings from SystemSettings
        2. Filter items by source languages (e.g., only 'en' and 'ja')
        3. Exclude items with existing derivations for target_locale
        4. Group items by lang_group
        5. For each lang_group:
           a. Sort by hotness DESC
           b. Select top X% (or small bucket rules)
        6. Combine and return up to `limit` items

    Example:
        >>> from shared.translation_selection import select_items_for_translation
        >>> items = select_items_for_translation('zh-Hans', limit=50)
        >>> print(f"Selected {len(items)} items for translation")
        Selected 35 items for translation

    Note:
        - Idempotent: Skips items with existing translations
        - Respects admin-configurable settings
        - Handles edge cases (empty groups, missing hotness scores)
    """
    # Import here to avoid circular dependency
    from crawler_admin.models import TrendItem, ItemDerivation

    # Get settings
    settings = get_translation_settings()
    source_langs = settings['source_langs']

    # Get target language group for same-language skip logic
    target_lang_group = locale_to_lang_group(target_locale)

    logger.info(
        f"Selecting items for translation to {target_locale}: "
        f"source_langs={source_langs if source_langs else 'ALL'}, "
        f"excluding target_lang_group={target_lang_group} (per-locale hot_percent)"
    )

    # Build base query:
    # - base_lang in source_langs (if specified), or ALL languages if empty
    # - hotness is not NULL (can't rank without hotness)
    # - Skip same-language translations (e.g., en→en, zh→zh)
    # - No existing derivation for target_locale
    if source_langs and len(source_langs) > 0:
        # Filter to specific source languages
        base_query = TrendItem.objects.filter(
            base_lang__in=source_langs,
            hotness__isnull=False,
        )
    else:
        # Select from ALL languages
        base_query = TrendItem.objects.filter(
            hotness__isnull=False,
        )

    # Pre-fetch related objects for async context compatibility
    # This prevents "SynchronousOnlyOperation" errors when accessing item.surface/region
    base_query = base_query.select_related('surface', 'region')

    # CRITICAL: Only translate items whose summarization is done.
    # Translating raw text before the AI summary is written produces stale translations
    # that persist forever (derivation_exists check blocks retry). Gate here ensures
    # every derivation is a translation of the summary, not the original raw text.
    base_query = base_query.filter(summary_status__in=['complete', 'skipped'])

    # Skip same-language translations (e.g., don't translate English→English)
    base_query = base_query.exclude(lang_group=target_lang_group)

    # Exclude items with existing complete translations using EXISTS subquery
    # This is more reliable than exclude(derivations__...) which can fail with complex conditions
    has_translation = ItemDerivation.objects.filter(
        item_id=OuterRef('id'),
        derivation_type='translation',
        target_locale=target_locale,
        status='complete'
    )
    base_query = base_query.exclude(Exists(has_translation))

    # Get all language groups from source items
    # CRITICAL FIX: Force evaluation to list to avoid distinct() bug
    # Without this, the QuerySet iterates over ALL items instead of distinct values
    lang_groups = list(set(base_query.values_list('lang_group', flat=True)))

    selected_items = []

    for lang_group in lang_groups:
        if not lang_group:
            continue

        # Use per-source-locale hot_percent (e.g. en=5%, ja=2%, ru=2%)
        by_locale = settings.get('hot_percent_by_locale', {})
        hot_percent_for_group = by_locale.get(lang_group, settings.get('hot_percent', 10))

        # Build query for ALL items in this lang_group (including already-translated)
        # This gives us the TRUE total for percentage calculation
        if source_langs and len(source_langs) > 0:
            all_items_query = TrendItem.objects.filter(
                base_lang__in=source_langs,
                lang_group=lang_group,
                hotness__isnull=False,
            )
        else:
            all_items_query = TrendItem.objects.filter(
                lang_group=lang_group,
                hotness__isnull=False,
            )

        # Exclude same-language translations (e.g., don't count English→English)
        all_items_query = all_items_query.exclude(lang_group=target_lang_group)

        # Count TOTAL items (including already-translated)
        total_count = all_items_query.count()

        if total_count == 0:
            logger.debug(f"No items in lang_group={lang_group}")
            continue

        # Count already-translated items for this lang_group
        already_translated = ItemDerivation.objects.filter(
            item__lang_group=lang_group,
            item__hotness__isnull=False,
            derivation_type='translation',
            target_locale=target_locale,
            status='complete'
        )
        if source_langs and len(source_langs) > 0:
            already_translated = already_translated.filter(item__base_lang__in=source_langs)

        # Exclude same-language translations from the count
        already_translated = already_translated.exclude(item__lang_group=target_lang_group)

        already_translated_count = already_translated.count()

        # Calculate TARGET count based on TOTAL items and per-source-locale percent
        target_count = calculate_translation_count(
            total_count, hot_percent_for_group
        )

        # Calculate how many MORE we need to translate
        needed_count = max(0, target_count - already_translated_count)

        logger.info(
            f"lang_group={lang_group}: total={total_count}, "
            f"hot_percent={hot_percent_for_group}%, "
            f"already_translated={already_translated_count}, "
            f"target={target_count}, needed={needed_count}"
        )

        if needed_count == 0:
            logger.debug(f"lang_group={lang_group}: quota already filled, skipping")
            continue

        # Now select from UNTRANSLATED items only
        # Use the existing base_query which already excludes translated items
        group_items_untranslated = base_query.filter(
            lang_group=lang_group
        ).order_by('-hotness')

        # Select top N untranslated items where N = needed_count
        selected = list(group_items_untranslated[:needed_count])
        selected_items.extend(selected)

        logger.info(
            f"Selected {len(selected)}/{total_count} items from lang_group={lang_group} "
            f"for translation to {target_locale}"
        )

    # Sort combined results by hotness DESC
    selected_items.sort(key=lambda item: item.hotness or 0.0, reverse=True)

    # Apply global limit
    if len(selected_items) > limit:
        logger.info(
            f"Limiting selection from {len(selected_items)} to {limit} items "
            f"(global limit)"
        )
        selected_items = selected_items[:limit]

    logger.info(
        f"Final selection: {len(selected_items)} items for translation to {target_locale}"
    )

    return selected_items


def pre_select_items_for_summarization(target_locale: str, limit: int = 500) -> int:
    """
    Mark top X% items per source locale as 'queued' so the summarization worker
    processes only the hottest items.

    This flips the pipeline dependency:
      OLD: summarize ALL → translate top X%
      NEW: pre-select top X% per locale → summarize only those → translate (non-zh only)

    Covers ALL lang_groups including zh:
    - zh items: queued for summarization only (no translation needed — already Chinese)
    - non-zh items: queued for summarization + will be translated to target_locale

    Hot percent is taken per-source-locale from hot_percent_by_locale[lang_group].

    Args:
        target_locale: Target translation locale (e.g. 'zh-Hans')
        limit: Safety cap on total items marked per call

    Returns:
        Number of items newly marked as 'queued'
    """
    from crawler_admin.models import TrendItem, ItemDerivation

    settings = get_translation_settings()
    source_langs = settings['source_langs']
    target_lang_group = locale_to_lang_group(target_locale)  # 'zh' for zh-Hans

    logger.info(
        f"Pre-selecting items for summarization (target={target_locale}): "
        f"source_langs={source_langs or 'ALL'}, per-locale hot_percent"
    )

    # Base query: pending items with hotness
    if source_langs:
        base_query = TrendItem.objects.filter(
            base_lang__in=source_langs,
            hotness__isnull=False,
            summary_status='pending',
        )
    else:
        base_query = TrendItem.objects.filter(
            hotness__isnull=False,
            summary_status='pending',
        )

    # Subquery: items that already have a complete translation (skip re-queuing for non-zh)
    has_translation = ItemDerivation.objects.filter(
        item_id=OuterRef('id'),
        derivation_type='translation',
        target_locale=target_locale,
        status='complete'
    )

    # Get all lang_groups in the pending pool (including zh)
    lang_groups = list(set(base_query.values_list('lang_group', flat=True)))

    total_queued = 0

    for lang_group in lang_groups:
        if not lang_group:
            continue

        is_target_lang = (lang_group == target_lang_group)  # True for zh

        # Per-source-locale hot_percent (e.g. zh=2%, en=5%, ja=2%)
        by_locale = settings.get('hot_percent_by_locale', {})
        hot_percent_for_group = by_locale.get(lang_group, settings.get('hot_percent', 10))

        # True total for this lang_group (denominator for the % calculation)
        if source_langs:
            total_in_group = TrendItem.objects.filter(
                base_lang__in=source_langs,
                lang_group=lang_group,
                hotness__isnull=False,
            ).count()
        else:
            total_in_group = TrendItem.objects.filter(
                lang_group=lang_group,
                hotness__isnull=False,
            ).count()

        if total_in_group == 0:
            continue

        target_count = calculate_translation_count(total_in_group, hot_percent_for_group)

        # Items already handled in the summarization pipeline for this group
        already_handled = TrendItem.objects.filter(
            lang_group=lang_group,
            hotness__isnull=False,
            summary_status__in=['queued', 'complete', 'skipped'],
        ).count()

        needed = max(0, target_count - already_handled)

        logger.debug(
            f"lang_group={lang_group}: total={total_in_group}, "
            f"hot_percent={hot_percent_for_group}%, "
            f"already_handled={already_handled}, target={target_count}, needed={needed}"
        )

        if needed == 0:
            continue

        # Pending pool for this group
        group_pending = base_query.filter(lang_group=lang_group)

        # Non-zh: also skip items already translated (no need to re-summarize)
        if not is_target_lang:
            group_pending = group_pending.exclude(Exists(has_translation))

        ids_to_queue = list(
            group_pending.order_by('-hotness')
            .values_list('id', flat=True)[:needed]
        )

        if not ids_to_queue:
            continue

        # Safety cap
        if total_queued + len(ids_to_queue) > limit:
            ids_to_queue = ids_to_queue[:limit - total_queued]

        updated = TrendItem.objects.filter(id__in=ids_to_queue).update(
            summary_status='queued'
        )
        total_queued += updated

        logger.info(
            f"lang_group={lang_group}: queued {updated} item(s) for summarization "
            f"(hot_percent={hot_percent_for_group}%, target={target_count}, total={total_in_group})"
        )

        if total_queued >= limit:
            logger.info(f"Pre-selection hit safety limit ({limit}), stopping early")
            break

    logger.info(
        f"Pre-selection complete: {total_queued} item(s) newly queued for summarization"
    )
    return total_queued


def get_translation_coverage_stats(target_locale: str) -> Dict[str, Any]:
    """
    Get translation coverage statistics for a target locale.

    Args:
        target_locale: Target locale (e.g., 'zh-Hans')

    Returns:
        Dict with coverage stats per language group:
        {
            'en': {
                'total_items': 1000,
                'items_with_hotness': 950,
                'translated': 95,
                'coverage_pct': 10.0
            },
            'ja': {
                'total_items': 500,
                'items_with_hotness': 480,
                'translated': 48,
                'coverage_pct': 10.0
            }
        }

    Note:
        - Useful for monitoring and validation
        - Coverage % should match translation_hot_percent setting
    """
    from crawler_admin.models import TrendItem, ItemDerivation
    from django.db.models import Count, Q

    settings = get_translation_settings()
    source_langs = settings['source_langs']

    stats = {}

    # If source_langs is empty list, get all distinct languages
    if source_langs and len(source_langs) > 0:
        base_langs = source_langs
    else:
        base_langs = TrendItem.objects.filter(
            hotness__isnull=False
        ).values_list('base_lang', flat=True).distinct()

    for base_lang in base_langs:
        # Total items for this language
        total = TrendItem.objects.filter(base_lang=base_lang).count()

        # Items with hotness
        with_hotness = TrendItem.objects.filter(
            base_lang=base_lang,
            hotness__isnull=False
        ).count()

        # Translated items
        translated = TrendItem.objects.filter(
            base_lang=base_lang,
            derivations__derivation_type='translation',
            derivations__target_locale=target_locale,
            derivations__status='complete'
        ).distinct().count()

        # Coverage percentage
        coverage_pct = (translated / with_hotness * 100.0) if with_hotness > 0 else 0.0

        stats[base_lang] = {
            'total_items': total,
            'items_with_hotness': with_hotness,
            'translated': translated,
            'coverage_pct': round(coverage_pct, 2)
        }

    return stats


def estimate_translation_workload(target_locale: str) -> Dict[str, Any]:
    """
    Estimate translation workload (items needing translation).

    Args:
        target_locale: Target locale (e.g., 'zh-Hans')

    Returns:
        Dict with workload estimate:
        {
            'pending_count': 150,
            'estimated_cost': 75.0,  # Assuming $0.50 per item
            'by_lang_group': {
                'en': 100,
                'ja': 50
            }
        }

    Note:
        - Helps admins understand translation budget impact
        - Assumes average cost of $0.50 per item (adjust as needed)
    """
    from crawler_admin.models import TrendItem

    settings = get_translation_settings()
    source_langs = settings['source_langs']

    # Get items that would be selected for translation
    selected = select_items_for_translation(target_locale, limit=999999)

    by_lang_group = {}
    for item in selected:
        lang_group = item.lang_group or 'unknown'
        by_lang_group[lang_group] = by_lang_group.get(lang_group, 0) + 1

    # Estimate cost (rough estimate: $0.50 per item)
    # Adjust based on actual translation provider pricing
    estimated_cost = len(selected) * 0.50

    return {
        'pending_count': len(selected),
        'estimated_cost': round(estimated_cost, 2),
        'by_lang_group': by_lang_group
    }
