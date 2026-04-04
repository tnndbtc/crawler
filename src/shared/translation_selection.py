"""
Translation and summarization pre-selection utilities.

get_translation_settings(): Read per-locale hot_percent from SystemSettings.
get_hotness_cutoff(): Shared helper — hotness threshold for top X% of a lang_group.
select_items_for_translation(): Pick untranslated items above the cutoff per lang_group.
pre_select_items_for_summarization(): Queue unsummarized items above the cutoff per lang_group.
"""

import logging
from datetime import timedelta
from typing import List, Dict, Any, Optional
from django.db.models import Exists, OuterRef
from django.utils import timezone

logger = logging.getLogger(__name__)


def get_translation_settings() -> Dict[str, Any]:
    """
    Read translation settings from SystemSettings.

    Returns dict with:
      hot_percent            - global fallback percentage
      hot_percent_by_locale  - per lang_group percentages (e.g. {'en': 2, 'zh': 2})
      target_locales         - e.g. ['zh-Hans']
      source_langs           - e.g. ['en', 'ja']  (empty = all)
    """
    from crawler_admin.models import SystemSettings

    global_pct = SystemSettings.get_setting('translation_hot_percent', default=10)
    KNOWN_LANG_GROUPS = ['zh', 'ja', 'ko', 'en', 'es', 'pt', 'fr', 'de',
                         'ar', 'hi', 'id', 'ru', 'tr', 'it', 'pl']
    locale_pcts = {
        lang: SystemSettings.get_setting(f'translation_hot_percent_{lang}', default=global_pct)
        for lang in KNOWN_LANG_GROUPS
    }
    return {
        'hot_percent': global_pct,
        'hot_percent_by_locale': locale_pcts,
        'target_locales': SystemSettings.get_setting('translation_target_locales', default=['zh-Hans']),
        'source_langs': SystemSettings.get_setting('translation_source_langs', default=['en', 'ja']),
    }


HOTNESS_WINDOW_HOURS = 24  # Rolling window for hotness cutoff calculation


def get_hotness_cutoff(lang_group: str, hot_percent: int) -> Optional[float]:
    """
    Return the minimum hotness value for items in the top hot_percent% of lang_group,
    computed within a 24-hour rolling window.

    Using a rolling window ensures new posts compete only against recent posts,
    not all historical items. This is the single shared implementation used by
    both summarization pre-selection and translation selection.

    Returns None if no items exist in the window for this lang_group.
    """
    from crawler_admin.models import TrendItem

    since = timezone.now() - timedelta(hours=HOTNESS_WINDOW_HOURS)

    window = TrendItem.objects.filter(
        lang_group=lang_group,
        hotness__isnull=False,
        collected_at__gte=since,
    )

    total = window.count()
    if total == 0:
        return None

    target = max(1, int(total * hot_percent / 100))
    cutoff_qs = list(
        window.order_by('-hotness')
        .values_list('hotness', flat=True)[target - 1:target]
    )
    return cutoff_qs[0] if cutoff_qs else None


def select_items_for_translation(target_locale: str, batch_size: int = 10) -> List:
    """
    Select up to batch_size items needing translation to target_locale.

    Per lang_group: find the top hot_percent% hotness cutoff, then pick all
    summarized+untranslated items at or above that cutoff.
    """
    from crawler_admin.models import TrendItem, ItemDerivation
    from shared.language_detection import locale_to_lang_group

    settings = get_translation_settings()
    source_langs = settings['source_langs']
    target_lang_group = locale_to_lang_group(target_locale)

    has_complete = ItemDerivation.objects.filter(
        item_id=OuterRef('id'),
        derivation_type='translation',
        target_locale=target_locale,
        status='complete'
    )

    pool = TrendItem.objects.filter(
        hotness__isnull=False,
        summary_status__in=['complete', 'skipped'],
    ).exclude(lang_group=target_lang_group)

    if source_langs:
        pool = pool.filter(base_lang__in=source_langs)

    selected = []

    for lang_group in set(pool.values_list('lang_group', flat=True)):
        if not lang_group:
            continue

        hot_percent = settings['hot_percent_by_locale'].get(lang_group, settings['hot_percent'])
        cutoff = get_hotness_cutoff(lang_group, hot_percent)
        if cutoff is None:
            continue

        logger.info(
            f"lang_group={lang_group}: hot_percent={hot_percent}%, cutoff={cutoff:.2f}"
        )

        items = list(
            pool.filter(lang_group=lang_group, hotness__gte=cutoff)
            .exclude(Exists(has_complete))
            .select_related('surface', 'region')
            .order_by('-hotness')
            [:batch_size]
        )
        selected.extend(items)

        if items:
            logger.info(
                f"Selected {len(items)} items from lang_group={lang_group} "
                f"for translation to {target_locale}"
            )

    selected.sort(key=lambda x: x.hotness or 0.0, reverse=True)
    result = selected[:batch_size]
    logger.info(f"Final selection: {len(result)} items for translation to {target_locale}")
    return result


def pre_select_items_for_summarization(target_locale: str, limit: int = 500) -> int:
    """
    Mark top X% hottest pending items per lang_group as 'queued' for summarization.

    Per lang_group: find the top hot_percent% hotness cutoff, then queue all
    pending items at or above that cutoff.
    """
    from crawler_admin.models import TrendItem

    settings = get_translation_settings()
    source_langs = settings['source_langs']

    pool = TrendItem.objects.filter(
        hotness__isnull=False,
        summary_status='pending',
    )
    if source_langs:
        pool = pool.filter(base_lang__in=source_langs)

    total_queued = 0

    for lang_group in set(pool.values_list('lang_group', flat=True)):
        if not lang_group or total_queued >= limit:
            continue

        hot_percent = settings['hot_percent_by_locale'].get(lang_group, settings['hot_percent'])
        cutoff = get_hotness_cutoff(lang_group, hot_percent)
        if cutoff is None:
            continue

        ids = list(
            pool.filter(lang_group=lang_group, hotness__gte=cutoff)
            .order_by('-hotness')
            .values_list('id', flat=True)
            [:limit - total_queued]
        )
        if not ids:
            continue

        updated = TrendItem.objects.filter(id__in=ids).update(summary_status='queued')
        total_queued += updated

        if updated:
            logger.info(
                f"lang_group={lang_group}: queued {updated} item(s) for summarization "
                f"(hot_percent={hot_percent}%, cutoff={cutoff:.2f})"
            )

    logger.info(f"Pre-selection complete: {total_queued} item(s) newly queued for summarization")
    return total_queued
