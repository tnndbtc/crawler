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
- translation_small_bucket_min: Min items for small buckets (default: 1)
- translation_small_bucket_max: Max items for small buckets (default: 5)
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
from django.db.models import Q

logger = logging.getLogger(__name__)


def get_translation_settings() -> Dict[str, Any]:
    """
    Get translation settings from SystemSettings model.

    Returns:
        Dict with translation settings:
        {
            'hot_percent': 10,
            'small_bucket_min': 1,
            'small_bucket_max': 5,
            'target_locales': ['zh-Hans'],
            'source_langs': ['en', 'ja']
        }

    Note:
        - Imports SystemSettings lazily to avoid circular imports
        - Uses sensible defaults if settings not found
    """
    # Import here to avoid circular dependency
    from crawler_admin.models import SystemSettings

    return {
        'hot_percent': SystemSettings.get_setting('translation_hot_percent', default=10),
        'small_bucket_min': SystemSettings.get_setting('translation_small_bucket_min', default=1),
        'small_bucket_max': SystemSettings.get_setting('translation_small_bucket_max', default=5),
        'target_locales': SystemSettings.get_setting('translation_target_locales', default=['zh-Hans']),
        'source_langs': SystemSettings.get_setting('translation_source_langs', default=['en', 'ja']),
    }


def apply_small_bucket_logic(total_count: int, hot_percent: int, small_min: int, small_max: int) -> int:
    """
    Apply small bucket logic for translation selection.

    For small groups (<20 items), use fixed min/max instead of percentage.

    Args:
        total_count: Total items in the language group
        hot_percent: Percentage to select (e.g., 10 for top 10%)
        small_min: Minimum items to select for small buckets
        small_max: Maximum items to select for small buckets

    Returns:
        Number of items to select

    Examples:
        >>> apply_small_bucket_logic(5, 10, 1, 5)
        1  # Small bucket: min 1

        >>> apply_small_bucket_logic(15, 10, 1, 5)
        1  # Small bucket: 10% of 15 = 1.5 → min 1

        >>> apply_small_bucket_logic(100, 10, 1, 5)
        10  # Normal: 10% of 100 = 10

        >>> apply_small_bucket_logic(1000, 10, 1, 5)
        100  # Normal: 10% of 1000 = 100
    """
    if total_count < 20:
        # Small bucket: use min/max bounds
        select_count = max(small_min, min(total_count, small_max))
        logger.debug(
            f"Small bucket logic: total={total_count}, "
            f"select={select_count} (min={small_min}, max={small_max})"
        )
    else:
        # Normal bucket: use percentage
        select_count = max(1, int(total_count * hot_percent / 100.0))
        logger.debug(
            f"Normal bucket logic: total={total_count}, "
            f"percent={hot_percent}% → select={select_count}"
        )

    return select_count


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
    hot_percent = settings['hot_percent']
    small_min = settings['small_bucket_min']
    small_max = settings['small_bucket_max']
    source_langs = settings['source_langs']

    logger.info(
        f"Selecting items for translation to {target_locale}: "
        f"hot_percent={hot_percent}%, source_langs={source_langs}"
    )

    # Build base query:
    # - base_lang in source_langs (e.g., ['en', 'ja'])
    # - hotness is not NULL (can't rank without hotness)
    # - No existing derivation for target_locale
    base_query = TrendItem.objects.filter(
        base_lang__in=source_langs,
        hotness__isnull=False,
    ).exclude(
        derivations__derivation_type='translation',
        derivations__target_locale=target_locale,
        derivations__status='complete'
    )

    # Get all language groups from source items
    lang_groups = base_query.values_list('lang_group', flat=True).distinct()

    selected_items = []

    for lang_group in lang_groups:
        if not lang_group:
            continue

        # Get items in this language group
        group_items = base_query.filter(
            lang_group=lang_group
        ).order_by('-hotness')  # Sort by hotness DESC

        total_count = group_items.count()

        if total_count == 0:
            logger.debug(f"No items in lang_group={lang_group}")
            continue

        # Apply selection logic
        select_count = apply_small_bucket_logic(
            total_count, hot_percent, small_min, small_max
        )

        # Select top items
        selected = list(group_items[:select_count])
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

    for base_lang in source_langs:
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
