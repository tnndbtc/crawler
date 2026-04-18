"""
Topic classifier worker — classifies TrendItems by topic.

Implements the classification state machine per class.txt design:

  pending → heuristic pass runs
    ├─ no category found (tier=none)
    │    → LLM eligible unconditionally → llm_complete or failed
    │
    └─ category found (classified_by ∈ {bucket, platform, keyword, locale, surface})
         ├─ tier=high/medium (bucket, platform, keyword) → heuristic_complete
         ├─ tier=low AND NOT high-hotness AND NOT platform-top → heuristic_complete
         └─ tier=low AND (high-hotness OR platform-top)      → LLM attempt
              ├─ LLM finds category → llm_complete
              └─ LLM fails         → heuristic_complete (heuristic result preserved)

LLM eligibility is now based on confidence tier, not just empty tags.
Low-confidence (locale/surface) + high-hotness items are sent to LLM for rescue.

Version management:
  On startup and after each auto_keywords.json hot-reload, compute
  current_version = "<TAXONOMY_VERSION>:<sha256(auto_keywords.json)[:8]>".
  Re-queue all non-LLM items where classification_version != current_version.
  This replaces reset_tried_no_tags().

Enable/disable via ENABLE_TOPIC_CLASSIFIER in .env.
LLM enable/disable via SystemSetting 'enable_llm_classification' (boolean, default True).
"""

import hashlib
import os
import sys
import time
import logging
import gc

import django
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

from django.db import close_old_connections, reset_queries
from django.utils import timezone
from crawler_admin.models import TrendItem, SystemSettings
from shared.topic_classifier import (
    classify_item,
    reload_keywords_if_updated,
    VALID_CATEGORIES,
    _AUTO_KEYWORDS_PATH,
)

logger = logging.getLogger(__name__)

POLL_INTERVAL = int(os.getenv('TOPIC_CLASSIFIER_POLL_INTERVAL', '600'))  # 10 min

# Taxonomy version — bump manually whenever the canonical category vocabulary changes.
TAXONOMY_VERSION = 1

# Confidence tier lookup — derived from classified_by signal name
_CONFIDENCE_TIER: dict[str, str] = {
    'bucket':   'high',
    'platform': 'medium',
    'keyword':  'medium',
    'locale':   'low',
    'surface':  'low',
    'llm':      'high',
}


def _get_current_version() -> str:
    """Compute current classification_version from taxonomy version + keyword map sha."""
    try:
        content = _AUTO_KEYWORDS_PATH.read_bytes()
        sha = hashlib.sha256(content).hexdigest()[:8]
    except FileNotFoundError:
        sha = '00000000'
    return f"{TAXONOMY_VERSION}:{sha}"


def _is_llm_enabled() -> bool:
    """Read LLM enable flag from SystemSetting (default True)."""
    try:
        val = SystemSettings.get_setting('enable_llm_classification', default='true')
        return str(val).lower() not in ('false', '0', 'no')
    except Exception:
        return True


def _get_locale_hot_percent(lang_group: str | None) -> int | None:
    """
    Get the per-locale hotness percent threshold for LLM classification.
    Reads translation_hot_percent_<lang_group> from SystemSettings.
    Returns None if the key is absent — callers must treat None as not high-hotness
    (fail safe: missing config → no LLM upgrade for that lang_group).
    Does NOT fall back to a global key or hardcoded default.
    """
    try:
        if lang_group:
            key = f'translation_hot_percent_{lang_group}'
            val = SystemSettings.get_setting(key, default=None)
            if val is not None:
                return int(val)
        return None  # key absent — fail safe
    except Exception:
        return None


def _is_high_hotness(item) -> bool:
    """
    Check if item meets the high-hotness threshold for LLM classification.
    Reads the same SystemSetting used by translation_worker.
    Returns False if threshold is absent for this lang_group (fail safe).
    """
    threshold = _get_locale_hot_percent(item.lang_group)
    if threshold is None:
        return False  # no threshold configured for this lang_group — not eligible
    if item.hotness is None:
        return False
    # threshold is a percentile cutoff — we use it as a minimum score floor
    return item.hotness >= threshold


def _confidence_tier(classified_by: str | None) -> str:
    """Return confidence tier for a classified_by signal value."""
    if classified_by is None:
        return 'none'
    return _CONFIDENCE_TIER.get(classified_by, 'none')


def _is_llm_eligible(
    classified_by: str | None,
    item,
    platform_top_k_ids: set[int] | None = None,
) -> bool:
    """
    Determine LLM eligibility based on confidence tier.

    tier=none  → eligible unconditionally (no heuristic result)
    tier=low   → eligible if item meets global high-hotness threshold OR is in
                 the top-K for its platform (platform_top_k_ids)
    tier=high/medium → not eligible
    """
    tier = _confidence_tier(classified_by)
    if tier == 'none':
        return True
    if tier == 'low':
        if _is_high_hotness(item):
            return True
        if platform_top_k_ids and item.id in platform_top_k_ids:
            return True
        return False
    return False


def _get_stored_classifier_version() -> str:
    """Read the last persisted classifier version from SystemSettings."""
    try:
        val = SystemSettings.get_setting('topic_classifier_version', default='')
        return str(val) if val is not None else ''
    except Exception:
        return ''


def _save_classifier_version(version: str) -> None:
    """Persist the current classifier version to SystemSettings."""
    try:
        SystemSettings.objects.update_or_create(
            key='topic_classifier_version',
            defaults={
                'value_json': version,
                'description': 'Last applied topic classifier version (TAXONOMY_VERSION:keywords_sha)',
            },
        )
    except Exception as e:
        logger.warning(f"Failed to persist classifier version: {e}")


def requeue_stale_items(current_version: str) -> int:
    """
    Reset classification_state to 'pending' for all non-LLM items where
    classification_version != current_version AND are not already pending.

    LLM items are exempt — their classifications are authoritative.
    Items already in 'pending' are skipped — no point resetting what is
    already pending (and avoids touching rows that have never been classified).
    Returns count of items re-queued.
    """
    count = TrendItem.objects.exclude(
        classification_version=current_version,
    ).exclude(
        classified_by='llm',
    ).exclude(
        classification_state='pending',
    ).update(
        classification_state='pending',
        story_category=None,
        classified_by=None,
        classification_version=None,
    )
    if count:
        logger.info(
            f"Version re-queue: reset {count} stale items to pending "
            f"(current_version={current_version})"
        )
    return count


def get_pending_items(per_platform: int | None = None) -> list:
    """
    Fetch top N pending items per platform, ordered by hotness desc.

    Uses topic_per_platform_limit SystemSetting (default 200) so every platform
    contributes proportionally — prevents high-volume platforms (YouTube, Bilibili)
    from crowding out niche platforms (HN, devto, paperswithcode) in the LLM queue.
    """
    if per_platform is None:
        try:
            per_platform = int(
                SystemSettings.get_setting('topic_per_platform_limit', default=200)
            )
        except Exception:
            per_platform = 200

    platforms = (
        TrendItem.objects
        .filter(classification_state='pending')
        .values_list('surface__platform', flat=True)
        .distinct()
    )

    result = []
    for platform in platforms:
        items = list(
            TrendItem.objects
            .filter(classification_state='pending', surface__platform=platform)
            .select_related('surface', 'region')
            # Defer large fields not needed for classification to prevent OOM.
            # raw_payload (avg 2.5 KB JSON) × 33,000 items = ~250 MB raw JSON,
            # which Python expands to ~750 MB–1 GB of dicts in memory.
            # engagement_signals, canonical_*, display_* are also unused here.
            .defer(
                'raw_payload', 'engagement_signals',
                'canonical_title', 'canonical_description', 'canonical_error',
                'display_title_zh_hans', 'display_description_zh_hans',
            )
            .order_by('-hotness')[:per_platform]
        )
        result.extend(items)

    result.sort(key=lambda x: x.hotness or 0, reverse=True)
    return result


def _compute_platform_top_k(items: list, k: int) -> set[int]:
    """
    Return item IDs that are in the top-K by hotness within their own platform.

    Used to promote niche-platform items into LLM eligibility even when they fall
    below the global hotness threshold — mirrors story_engine's per-platform top-K
    windowing logic.
    """
    from collections import defaultdict
    by_platform: dict[str, list] = defaultdict(list)
    for item in items:
        platform = item.surface.platform if item.surface else 'unknown'
        by_platform[platform].append(item)

    top_k_ids: set[int] = set()
    for group in by_platform.values():
        group.sort(key=lambda x: x.hotness or 0, reverse=True)
        for item in group[:k]:
            top_k_ids.add(item.id)
    return top_k_ids


def run_once(current_version: str) -> None:
    """Run one classification cycle."""
    items = get_pending_items()
    if not items:
        logger.debug("No pending items found")
        return

    logger.info(f"Found {len(items)} pending items")

    enable_llm = _is_llm_enabled()
    now = timezone.now()

    # Load LLM classifier if enabled
    classify_batch_llm = None
    BATCH_SIZE = 20
    if enable_llm:
        try:
            from shared.topic_llm_classifier import classify_batch_llm, BATCH_SIZE
        except ImportError:
            logger.info("topic_llm_classifier not available — LLM pass disabled")
            enable_llm = False

    # Compute platform-top-K set for LLM eligibility promotion.
    # Items in the top-K of their platform bypass the global hotness threshold
    # so niche platforms (HN, devto, paperswithcode) get LLM coverage.
    try:
        llm_top_k = int(
            SystemSettings.get_setting('topic_llm_top_k_per_platform', default=50)
        )
    except Exception:
        llm_top_k = 50
    platform_top_k_ids = _compute_platform_top_k(items, llm_top_k)
    logger.info(
        f"Platform top-K: k={llm_top_k}, eligible_ids={len(platform_top_k_ids)}"
    )

    # Buckets for bulk updates
    heuristic_complete: list = []
    needs_llm: list[tuple] = []   # (item, heuristic_cat, heuristic_signal) or (item, None, None)
    failed_items: list = []        # items with no category AND LLM disabled

    # --- Heuristic pass ---
    for item in items:
        cat, signal = classify_item(
            bucket=item.bucket or '',
            platform=item.surface.platform if item.surface else '',
            surface_key=item.surface.key if item.surface else '',
            lang_group=item.lang_group,
            title=item.title_original or '',
            description=item.description_original or None,
        )

        if cat:
            # Category found — check LLM eligibility via confidence tier
            if not _is_llm_eligible(signal, item, platform_top_k_ids):
                # tier=high/medium, or tier=low + not high-hotness + not platform-top → heuristic_complete
                item.story_category = cat
                item.classified_by = signal
                item.classification_state = 'heuristic_complete'
                item.classification_version = current_version
                item.topic_classified_at = now
                heuristic_complete.append(item)
            else:
                # tier=low AND (high-hotness OR platform-top) → attempt LLM improvement
                if enable_llm:
                    needs_llm.append((item, cat, signal))
                else:
                    # LLM disabled → write heuristic result immediately
                    item.story_category = cat
                    item.classified_by = signal
                    item.classification_state = 'heuristic_complete'
                    item.classification_version = current_version
                    item.topic_classified_at = now
                    heuristic_complete.append(item)
        else:
            # No category found
            if enable_llm:
                needs_llm.append((item, None, None))
            else:
                item.classification_state = 'failed'
                item.topic_classified_at = now
                failed_items.append(item)

    # Bulk write heuristic_complete items
    if heuristic_complete:
        TrendItem.objects.bulk_update(
            heuristic_complete,
            ['story_category', 'classified_by', 'classification_state',
             'classification_version', 'topic_classified_at'],
            batch_size=500,
        )
        logger.info(f"Heuristic: {len(heuristic_complete)} items → heuristic_complete")

    # Bulk write failed items (LLM disabled)
    if failed_items:
        TrendItem.objects.bulk_update(
            failed_items,
            ['classification_state', 'topic_classified_at'],
            batch_size=500,
        )
        logger.info(f"Failed (LLM disabled): {len(failed_items)} items → failed")

    if not needs_llm:
        return

    # --- LLM pass ---
    llm_items_no_heuristic = [(item, None, None) for item, cat, sig in needs_llm if cat is None]
    llm_items_low_conf = [(item, cat, sig) for item, cat, sig in needs_llm if cat is not None]

    logger.info(
        f"LLM pass: {len(llm_items_no_heuristic)} no-category items + "
        f"{len(llm_items_low_conf)} low-confidence items"
    )

    llm_complete_count = 0
    heuristic_fallback_count = 0
    llm_failed_count = 0

    for chunk in [llm_items_no_heuristic, llm_items_low_conf]:
        for i in range(0, len(chunk), BATCH_SIZE):
            batch = chunk[i:i + BATCH_SIZE]
            batch_items = [t[0] for t in batch]
            batch_heuristic = [(t[1], t[2]) for t in batch]

            batch_dicts = [
                {
                    'title': item.title_original or '',
                    'platform': item.surface.platform if item.surface else '',
                    'description': (item.description_original or '')[:300],
                }
                for item in batch_items
            ]

            results = classify_batch_llm(batch_dicts)
            to_update = []

            for item, llm_cat, (heuristic_cat, heuristic_signal) in zip(
                batch_items, results, batch_heuristic
            ):
                item.topic_classified_at = now

                if llm_cat:
                    # LLM found a category
                    item.story_category = llm_cat
                    item.classified_by = 'llm'
                    item.classification_state = 'llm_complete'
                    item.classification_version = current_version
                    llm_complete_count += 1
                elif heuristic_cat:
                    # LLM failed but heuristic had a result — preserve it
                    item.story_category = heuristic_cat
                    item.classified_by = heuristic_signal
                    item.classification_state = 'heuristic_complete'
                    item.classification_version = current_version
                    heuristic_fallback_count += 1
                else:
                    # No category from either pass → failed
                    item.story_category = None
                    item.classification_state = 'failed'
                    llm_failed_count += 1

                to_update.append(item)

            TrendItem.objects.bulk_update(
                to_update,
                ['story_category', 'classified_by', 'classification_state',
                 'classification_version', 'topic_classified_at'],
                batch_size=500,
            )

    logger.info(
        f"LLM pass complete: {llm_complete_count} llm_complete, "
        f"{heuristic_fallback_count} heuristic_fallback (LLM failed, heuristic preserved), "
        f"{llm_failed_count} failed"
    )


def run_worker_loop():
    """Main worker loop — polls every POLL_INTERVAL seconds."""
    logger.info("Topic classifier worker started")
    logger.info(f"POLL_INTERVAL={POLL_INTERVAL}s, TAXONOMY_VERSION={TAXONOMY_VERSION}")

    # Compute initial version.
    # Only re-queue stale items if the version changed since last run — this avoids
    # a full-table UPDATE on every restart (which created a massive SQLite WAL entry
    # when classification_version was first introduced and all rows had NULL).
    current_version = _get_current_version()
    stored_version = _get_stored_classifier_version()
    logger.info(
        f"Startup: current_version={current_version}, stored_version={stored_version!r}"
    )
    if stored_version != current_version:
        logger.info("Version changed — re-queuing stale items")
        requeue_stale_items(current_version)
        _save_classifier_version(current_version)
    else:
        logger.info("Version unchanged — skipping requeue (no WAL write needed)")

    while True:
        try:
            # Hot-reload: check if auto_keywords.json changed
            if reload_keywords_if_updated():
                current_version = _get_current_version()
                logger.info(f"Keywords reloaded: new current_version={current_version}")
                requeue_stale_items(current_version)
                _save_classifier_version(current_version)

            run_once(current_version)
        except Exception as e:
            logger.error(f"Topic classification cycle error: {e}", exc_info=True)
        finally:
            # Release Django ORM objects and DB connection state accumulated during
            # this cycle. Without this, select_related() fetches across hundreds of
            # TrendItems grow the process RSS by ~100–300 MB per cycle — over 15+
            # cycles this caused a 9 GB OOM kill on 2026-04-17.
            #
            # close_old_connections(): closes the DB connection so the next cycle
            #   gets a fresh one, clearing Django's per-connection query cache and
            #   any internal ORM state pinned to the connection object.
            # reset_queries(): clears django.db.connection.queries (only non-empty
            #   when DEBUG=True, but cheap to call regardless).
            # gc.collect(): releases any ORM objects the Python GC missed due to
            #   reference cycles in Django model instances.
            close_old_connections()
            reset_queries()
            gc.collect()

        time.sleep(POLL_INTERVAL)


def main():
    try:
        run_worker_loop()
    except KeyboardInterrupt:
        logger.info("Topic classifier worker stopped by user")
    except Exception as e:
        logger.error(f"Fatal worker error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
