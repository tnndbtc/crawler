"""
Translation and summarization pre-selection utilities.

get_translation_settings(): Read per-locale hot_percent from SystemSettings.
get_hotness_cutoff(): Shared helper — hotness threshold for top X% of a lang_group.
get_lang_group_pool_size(): Count items in the top-N% window for a lang_group.
get_surface_lang_group(): Derive dominant lang_group for a surface from recent TrendItems.
select_items_for_translation(): Two-pass selection (floor + competition) per surface.
pre_select_items_for_summarization(): Two-pass queue of unsummarized items.
"""

import logging
from datetime import timedelta
from typing import List, Dict, Any, Optional, Tuple
from django.db.models import Exists, OuterRef, Count
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
                         'ar', 'hi', 'id', 'ru', 'tr', 'it', 'pl',
                         'nl', 'sv', 'vi', 'th', 'ms']
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


def get_lang_group_pool_size(lang_group: str, hot_percent: int) -> int:
    """
    Count items in the top-N% hotness window for a given lang_group.

    Used to convert floor_percent / cap_percent into absolute item counts.
    Returns 0 if no items exist in the window.
    """
    from crawler_admin.models import TrendItem

    since = timezone.now() - timedelta(hours=HOTNESS_WINDOW_HOURS)
    cutoff = get_hotness_cutoff(lang_group, hot_percent)
    if cutoff is None:
        return 0
    return TrendItem.objects.filter(
        lang_group=lang_group,
        hotness__gte=cutoff,
        collected_at__gte=since,
    ).count()


def get_surface_lang_group(surface_id: int, since) -> Optional[str]:
    """
    Derive the dominant lang_group for a surface from its recent TrendItems.

    Queries items collected since `since`; picks the lang_group with the most items.
    Returns None if the surface has no recent items (crawler hasn't run yet, or
    the surface is new).
    """
    from crawler_admin.models import TrendItem

    result = (
        TrendItem.objects
        .filter(surface_id=surface_id, collected_at__gte=since)
        .values('lang_group')
        .annotate(n=Count('id'))
        .order_by('-n')
        .first()
    )
    return result['lang_group'] if result else None


def _load_surface_config() -> List:
    """
    Load allocation config for all enabled surfaces.
    Only fetches fields needed for floor/cap computation.
    """
    from crawler_admin.models import TrendSurface

    return list(
        TrendSurface.objects.filter(enabled=True).only(
            'id', 'key', 'surface_type', 'floor_percent', 'cap_percent'
        )
    )


def _precompute_surface_limits(
    surfaces: List,
    hot_percent_by_locale: Dict[str, int],
    global_hot_percent: int,
    since,
) -> Tuple[Dict, Dict, Dict]:
    """
    For each surface with floor_percent > 0 or cap_percent set:
      - Derive lang_group from recent TrendItems (Option B: runtime derivation)
      - Compute pool_size for that lang_group (cached per lang_group)
      - Convert percentages to absolute item counts

    Returns:
        surface_lang:  {surface_id → lang_group}
        surface_floor: {surface_id → absolute floor count (>= 1 if floor_percent > 0)}
        surface_cap:   {surface_id → absolute cap count, or None if uncapped}
    """
    surface_lang: Dict[int, str] = {}
    surface_floor: Dict[int, int] = {}
    surface_cap: Dict[int, Optional[int]] = {}
    pool_size_cache: Dict[str, int] = {}

    for s in surfaces:
        needs_floor = s.floor_percent > 0
        needs_cap = s.cap_percent is not None
        if not needs_floor and not needs_cap:
            continue

        lang_group = get_surface_lang_group(s.id, since)
        if not lang_group:
            logger.debug(f"Surface {s.key}: no recent items in window, skipping allocation")
            continue

        if lang_group not in pool_size_cache:
            hp = hot_percent_by_locale.get(lang_group, global_hot_percent)
            pool_size_cache[lang_group] = get_lang_group_pool_size(lang_group, hp)

        pool = pool_size_cache[lang_group]
        if pool == 0:
            logger.debug(f"Surface {s.key}: lang_group={lang_group} pool is empty, skipping")
            continue

        surface_lang[s.id] = lang_group
        surface_floor[s.id] = max(1, int(pool * s.floor_percent / 100)) if needs_floor else 0
        surface_cap[s.id] = int(pool * s.cap_percent / 100) if needs_cap else None

        logger.info(
            f"Surface {s.key}: lang_group={lang_group}, pool={pool}, "
            f"floor={surface_floor.get(s.id, 0)}, cap={surface_cap.get(s.id)}"
        )

    return surface_lang, surface_floor, surface_cap


def select_items_for_translation(target_locale: str, batch_size: int = 10) -> List:
    """
    Select up to batch_size items needing translation to target_locale.

    Two-pass strategy:
      PASS 1 — Floor guarantee: surfaces with floor_percent > 0 get a minimum number
               of slots, ordered by recency (rss_feed surfaces) or hotness (others).
               Floor items bypass the source_langs filter.
      PASS 2 — Competition: remaining slots filled by standard per-lang_group hotness
               competition with cap_percent enforcement.
    """
    from crawler_admin.models import TrendItem, ItemDerivation
    from shared.language_detection import locale_to_lang_group

    settings = get_translation_settings()
    source_langs = settings['source_langs']
    target_lang_group = locale_to_lang_group(target_locale)
    hot_percent_by_locale = settings['hot_percent_by_locale']
    global_hot_percent = settings['hot_percent']

    since = timezone.now() - timedelta(hours=HOTNESS_WINDOW_HOURS)

    has_complete = ItemDerivation.objects.filter(
        item_id=OuterRef('id'),
        derivation_type='translation',
        target_locale=target_locale,
        status='complete'
    )

    surfaces = _load_surface_config()
    surface_lang, surface_floor, surface_cap = _precompute_surface_limits(
        surfaces, hot_percent_by_locale, global_hot_percent, since
    )
    surface_counts: Dict[int, int] = {}

    selected: List = []
    selected_ids: set = set()

    # ── PASS 1: Floor guarantee ───────────────────────────────────────────────
    surfaces_with_floor = sorted(
        [s for s in surfaces if s.floor_percent > 0 and s.id in surface_floor],
        key=lambda s: s.floor_percent,
        reverse=True,
    )

    for s in surfaces_with_floor:
        remaining_batch = batch_size - len(selected)
        if remaining_batch <= 0:
            break

        want = surface_floor[s.id]
        cap = surface_cap.get(s.id)
        if cap is not None:
            want = min(want, cap)
        want = min(want, remaining_batch)

        # Floor bypasses source_langs but still excludes self-translation
        base_qs = (
            TrendItem.objects
            .filter(
                hotness__isnull=False,
                summary_status__in=['complete', 'skipped'],
                surface_id=s.id,
            )
            .exclude(lang_group=target_lang_group)
            .exclude(Exists(has_complete))
        )

        # rss_feed: recency is the correct semantic (no engagement → hotness ≈ decay only)
        # ranking/news/other: hotness reflects real engagement
        order_field = '-published_at' if s.surface_type == 'rss_feed' else '-hotness'
        floor_items = list(base_qs.order_by(order_field)[:want])

        for item in floor_items:
            if item.id not in selected_ids:
                selected.append(item)
                selected_ids.add(item.id)
                surface_counts[s.id] = surface_counts.get(s.id, 0) + 1

        if floor_items:
            logger.info(
                f"PASS 1 translation: surface={s.key}, floor={surface_floor[s.id]}, "
                f"fetched={len(floor_items)}, order={order_field}"
            )

    # ── PASS 2: Competition ───────────────────────────────────────────────────
    remaining = batch_size - len(selected)
    if remaining <= 0:
        logger.info(f"Final selection: {len(selected)} items for translation to {target_locale}")
        return selected

    pool = (
        TrendItem.objects
        .filter(
            hotness__isnull=False,
            summary_status__in=['complete', 'skipped'],
        )
        .exclude(lang_group=target_lang_group)
    )
    if source_langs:
        pool = pool.filter(base_lang__in=source_langs)

    candidate_ids: List[int] = []
    for lang_group in set(pool.values_list('lang_group', flat=True)):
        if not lang_group:
            continue
        hp = hot_percent_by_locale.get(lang_group, global_hot_percent)
        cutoff = get_hotness_cutoff(lang_group, hp)
        if cutoff is None:
            continue
        logger.info(f"lang_group={lang_group}: hot_percent={hp}%, cutoff={cutoff:.2f}")
        ids = list(
            pool.filter(lang_group=lang_group, hotness__gte=cutoff)
            .exclude(id__in=selected_ids)
            .exclude(Exists(has_complete))
            .values_list('id', flat=True)
        )
        candidate_ids.extend(ids)

    candidate_ids = list(set(candidate_ids))

    if candidate_ids:
        fetch_limit = remaining + batch_size  # buffer for cap skips
        candidates = list(
            TrendItem.objects
            .filter(id__in=candidate_ids)
            .select_related('surface', 'region')
            .order_by('-hotness')[:fetch_limit]
        )

        for item in candidates:
            if len(selected) >= batch_size:
                break
            if item.id in selected_ids:
                continue
            cap = surface_cap.get(item.surface_id)
            if cap is not None and surface_counts.get(item.surface_id, 0) >= cap:
                continue
            selected.append(item)
            selected_ids.add(item.id)
            surface_counts[item.surface_id] = surface_counts.get(item.surface_id, 0) + 1

    logger.info(f"Final selection: {len(selected)} items for translation to {target_locale}")
    return selected


def pre_select_items_for_summarization(target_locale: str, limit: int = 500) -> int:
    """
    Mark top-N% hottest pending items per lang_group as 'queued' for summarization.

    Two-pass strategy:
      PASS 1 — Floor guarantee: surfaces with floor_percent > 0 get minimum slots
               queued, ordered by recency (rss_feed) or hotness (others).
               Floor items bypass the source_langs filter.
      PASS 2 — Competition: remaining limit filled by standard per-lang_group hotness
               competition with cap_percent enforcement.

    Note: target_locale is used only to read hot_percent settings; this function
    queues items across ALL lang_groups, not just the target locale's language.
    """
    from crawler_admin.models import TrendItem

    settings = get_translation_settings()
    source_langs = settings['source_langs']
    hot_percent_by_locale = settings['hot_percent_by_locale']
    global_hot_percent = settings['hot_percent']

    since = timezone.now() - timedelta(hours=HOTNESS_WINDOW_HOURS)

    surfaces = _load_surface_config()
    surface_lang, surface_floor, surface_cap = _precompute_surface_limits(
        surfaces, hot_percent_by_locale, global_hot_percent, since
    )
    surface_counts: Dict[int, int] = {}

    total_queued = 0
    queued_ids: set = set()

    # ── PASS 1: Floor guarantee ───────────────────────────────────────────────
    surfaces_with_floor = sorted(
        [s for s in surfaces if s.floor_percent > 0 and s.id in surface_floor],
        key=lambda s: s.floor_percent,
        reverse=True,
    )

    for s in surfaces_with_floor:
        if total_queued >= limit:
            break

        want = surface_floor[s.id]
        cap = surface_cap.get(s.id)
        if cap is not None:
            want = min(want, cap)
        want = min(want, limit - total_queued)

        # Floor items bypass source_langs entirely
        base_qs = TrendItem.objects.filter(
            hotness__isnull=False,
            summary_status='pending',
            surface_id=s.id,
        )

        order_field = '-published_at' if s.surface_type == 'rss_feed' else '-hotness'
        ids = list(base_qs.order_by(order_field).values_list('id', flat=True)[:want])

        if ids:
            updated = TrendItem.objects.filter(id__in=ids).update(summary_status='queued')
            total_queued += updated
            queued_ids.update(ids)
            surface_counts[s.id] = surface_counts.get(s.id, 0) + updated
            logger.info(
                f"PASS 1 summarization: surface={s.key}, floor={surface_floor[s.id]}, "
                f"queued={updated}, order={order_field}"
            )

    # ── PASS 2: Competition ───────────────────────────────────────────────────
    if total_queued >= limit:
        logger.info(f"Pre-selection complete: {total_queued} item(s) newly queued for summarization")
        return total_queued

    pool = TrendItem.objects.filter(
        hotness__isnull=False,
        summary_status='pending',
    ).exclude(id__in=queued_ids)

    if source_langs:
        pool = pool.filter(base_lang__in=source_langs)

    for lang_group in set(pool.values_list('lang_group', flat=True)):
        if not lang_group or total_queued >= limit:
            continue

        hp = hot_percent_by_locale.get(lang_group, global_hot_percent)
        cutoff = get_hotness_cutoff(lang_group, hp)
        if cutoff is None:
            continue

        # Fetch candidates above cutoff with a small buffer for cap skips
        fetch_limit = (limit - total_queued) + 50
        candidates = list(
            pool.filter(lang_group=lang_group, hotness__gte=cutoff)
            .order_by('-hotness')
            .values_list('id', 'surface_id')
            [:fetch_limit]
        )

        ids_to_queue: List[int] = []
        for item_id, surface_id in candidates:
            if total_queued + len(ids_to_queue) >= limit:
                break
            if item_id in queued_ids:
                continue
            cap = surface_cap.get(surface_id)
            if cap is not None and surface_counts.get(surface_id, 0) >= cap:
                continue
            ids_to_queue.append(item_id)
            surface_counts[surface_id] = surface_counts.get(surface_id, 0) + 1

        if ids_to_queue:
            updated = TrendItem.objects.filter(id__in=ids_to_queue).update(summary_status='queued')
            total_queued += updated
            queued_ids.update(ids_to_queue)
            logger.info(
                f"PASS 2 summarization: lang_group={lang_group}, queued={updated} "
                f"(cutoff={cutoff:.2f})"
            )

    logger.info(f"Pre-selection complete: {total_queued} item(s) newly queued for summarization")
    return total_queued
