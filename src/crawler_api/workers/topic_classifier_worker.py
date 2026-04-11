"""
Topic classifier worker — classifies TrendItems by topic.

Runs heuristic pass first (instant, no LLM cost), then LLM pass for
remaining ambiguous items that are within the top N% hotness per locale
(same threshold as translation: translation_hot_percent_<lang_group>).

Polls every 10 minutes for unclassified items (topic_tags=[], topic_classified_at IS NULL).

Hot-reload: each cycle checks if auto_keywords.json has changed. If so,
patterns are reloaded and all "Tried, no tags" items are reset so the
heuristic gets a second chance before LLM runs — saving LLM cost.

Enable/disable via ENABLE_TOPIC_CLASSIFIER in .env.
LLM pass enable/disable via ENABLE_TOPIC_LLM in .env.
"""

import os
import sys
import time
import logging

import django
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

from django.utils import timezone
from crawler_admin.models import TrendItem
from shared.topic_classifier import classify_topic_tags, reload_keywords_if_updated, VALID_TOPICS

logger = logging.getLogger(__name__)

POLL_INTERVAL = int(os.getenv('TOPIC_CLASSIFIER_POLL_INTERVAL', '600'))  # 10 min
ENABLE_LLM = os.getenv('ENABLE_TOPIC_LLM', 'false').lower() == 'true'  # off until Phase 4


def get_unclassified_items(limit: int = 5000) -> list:
    """
    Get items where topic classification was never attempted.
    Excludes items already attempted (topic_classified_at IS NOT NULL).
    """
    return list(
        TrendItem.objects.filter(
            topic_tags=[],
            topic_classified_at__isnull=True,
        )
        .select_related('surface', 'region')
        .order_by('-hotness')[:limit]
    )


def run_heuristic_pass(items: list) -> tuple[int, list]:
    """
    Run heuristic topic classification on items.

    Returns (classified_count, remaining_items).
    Items the heuristic couldn't classify are passed to LLM pass.

    Uses bulk_update to write all classified items in batches of 500
    instead of one UPDATE per item — avoids DB pressure at large batch sizes.
    """
    classified_items = []
    remaining = []
    now = timezone.now()

    for item in items:
        tags = classify_topic_tags(
            bucket=item.bucket or '',
            platform=item.surface.platform if item.surface else '',
            surface_key=item.surface.key if item.surface else '',
            lang_group=item.lang_group,
            title=item.title_original or '',
            description=item.description_original or None,
        )

        if tags:
            item.topic_tags = tags
            item.topic_classified_at = now
            item.classified_by = 'heuristic'
            classified_items.append(item)
        else:
            remaining.append(item)

    if classified_items:
        TrendItem.objects.bulk_update(
            classified_items,
            ['topic_tags', 'topic_classified_at', 'classified_by'],
            batch_size=500,
        )
        logger.debug(f"bulk_update wrote {len(classified_items)} classified items")

    return len(classified_items), remaining


# ---------------------------------------------------------------------------
# LLM pass helpers (Phase 4 — disabled by default until Phase 4 is deployed)
# ---------------------------------------------------------------------------

def _get_locale_hot_percent(lang_group: str | None) -> int:
    """
    Get the per-locale hotness percent threshold for LLM classification.
    Reads translation_hot_percent_<lang_group> from SystemSettings.
    Falls back to translation_hot_percent (global) then to 10.

    Uses the same setting as translation_worker.py so both LLM operations
    run on the same item population — no separate threshold to maintain.
    """
    try:
        from crawler_admin.models import SystemSettings
        if lang_group:
            key = f'translation_hot_percent_{lang_group}'
            val = SystemSettings.get_setting(key, default=None)
            if val is not None:
                return int(val)
        return int(SystemSettings.get_setting('translation_hot_percent', default=10))
    except Exception:
        return 10


def _filter_top_percent_per_locale(items: list) -> list:
    """
    Keep only the top N% by hotness WITHIN EACH lang_group.

    Each locale has its own translation_hot_percent_<locale> setting.
    Items are already sorted by -hotness, so we group by lang_group and
    keep the top N% of each group.
    """
    if not items:
        return items

    by_locale: dict[str, list] = {}
    for item in items:
        lg = item.lang_group or 'unknown'
        by_locale.setdefault(lg, []).append(item)

    kept = []
    for lg, group in by_locale.items():
        percent = _get_locale_hot_percent(lg)
        cutoff = max(1, len(group) * percent // 100)
        kept.extend(group[:cutoff])
        logger.info(f"  Topic LLM top {percent}% for lang={lg}: {cutoff}/{len(group)}")

    return kept


def run_llm_pass(items: list) -> int:
    """
    Run LLM topic classification on remaining ambiguous items.

    Only processes items in the top N% hotness per locale
    (translation_hot_percent_<lang_group>). Returns classified_count.

    NOTE: LLM classifier (topic_llm_classifier.py) is implemented in Phase 4.
    This function is a no-op stub until that module exists.
    """
    if not ENABLE_LLM:
        logger.info("Topic LLM classification disabled (ENABLE_TOPIC_LLM=false)")
        return 0

    try:
        from shared.topic_llm_classifier import classify_batch_llm, BATCH_SIZE
    except ImportError:
        logger.info("topic_llm_classifier not yet available (Phase 4) — skipping LLM pass")
        return 0

    items = _filter_top_percent_per_locale(items)
    logger.info(f"Topic LLM total after per-locale filtering: {len(items)} items")

    classified = 0
    now = timezone.now()

    for i in range(0, len(items), BATCH_SIZE):
        batch = items[i:i + BATCH_SIZE]

        batch_dicts = [
            {
                'title': item.title_original or '',
                'platform': item.surface.platform if item.surface else '',
                'description': (item.description_original or '')[:300],
            }
            for item in batch
        ]

        results = classify_batch_llm(batch_dicts)

        for item, tags in zip(batch, results):
            item.topic_classified_at = now
            item.classified_by = 'llm'
            if tags:
                item.topic_tags = sorted(set(tags) & VALID_TOPICS)
                item.save(update_fields=['topic_tags', 'topic_classified_at', 'classified_by'])
                classified += 1
            else:
                # LLM returned no tags — mark as attempted to avoid re-processing
                item.save(update_fields=['topic_classified_at', 'classified_by'])

    return classified


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_once():
    """Run one classification cycle."""
    items = get_unclassified_items()
    if not items:
        logger.debug("No unclassified items found")
        return

    logger.info(f"Found {len(items)} unclassified items")

    # Heuristic pass (instant, no LLM cost)
    heuristic_count, remaining = run_heuristic_pass(items)
    logger.info(f"Heuristic: classified {heuristic_count}, remaining {len(remaining)}")

    if not remaining:
        return

    # LLM pass — only for top N% per locale, disabled until Phase 4
    llm_sent_items = _filter_top_percent_per_locale(remaining)
    llm_sent_ids = {item.id for item in llm_sent_items}
    llm_count = 0

    if llm_sent_items:
        llm_count = run_llm_pass(remaining)  # run_llm_pass re-applies the filter
    logger.info(
        f"LLM: classified {llm_count}, "
        f"sent {len(llm_sent_items)}, "
        f"unclassified {len(remaining) - llm_count}"
    )

    # Mark low-hotness items NOT sent to LLM as attempted too.
    # They failed the heuristic and are below the hotness threshold for LLM —
    # marking them prevents infinite re-polling.
    now = timezone.now()
    not_sent = [item for item in remaining if item.id not in llm_sent_ids]
    if not_sent:
        for item in not_sent:
            item.topic_classified_at = now
        TrendItem.objects.bulk_update(not_sent, ['topic_classified_at'], batch_size=500)
        logger.info(f"Marked {len(not_sent)} low-hotness items as tried (skip LLM)")


def reset_tried_no_tags() -> int:
    """
    Reset topic_classified_at for all "Tried, no tags" items so they re-enter
    the heuristic queue on the next cycle.

    Called after a keyword hot-reload so newly added keywords get a chance to
    classify items that previously fell through — before spending LLM budget.

    Returns the number of items reset.
    """
    count = TrendItem.objects.filter(
        topic_tags=[],
        topic_classified_at__isnull=False,
    ).update(topic_classified_at=None, classified_by=None)
    if count:
        logger.info(
            f"Keyword hot-reload: reset {count} 'Tried, no tags' items "
            f"→ heuristic will re-run on next cycle (LLM only for remaining misses)"
        )
    return count


def run_worker_loop():
    """Main worker loop — polls every POLL_INTERVAL seconds."""
    logger.info("Topic classifier worker started")
    logger.info(f"POLL_INTERVAL={POLL_INTERVAL}s, ENABLE_LLM={ENABLE_LLM}")

    while True:
        try:
            # Hot-reload keywords if auto_keywords.json changed.
            # On reload, reset "Tried, no tags" so heuristic runs first (free)
            # before LLM is considered for remaining misses.
            if reload_keywords_if_updated():
                reset_tried_no_tags()

            run_once()
        except Exception as e:
            logger.error(f"Topic classification cycle error: {e}", exc_info=True)

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
