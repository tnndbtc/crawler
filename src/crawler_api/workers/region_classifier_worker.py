"""
Region classifier worker — classifies TrendItems by content region.

Runs heuristic pass first (instant), then LLM pass for remaining items.
Polls every 10 minutes for unclassified items (content_regions = []).

Enable/disable via ENABLE_REGION_CLASSIFIER in .env.
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
from shared.region_classifier import classify_content_regions
from shared.region_llm_classifier import classify_batch_llm, BATCH_SIZE

logger = logging.getLogger(__name__)

POLL_INTERVAL = int(os.getenv('REGION_CLASSIFIER_POLL_INTERVAL', '600'))  # 10 min
ENABLE_LLM = os.getenv('ENABLE_REGION_LLM', 'true').lower() == 'true'


def get_unclassified_items(limit: int = 500) -> list:
    """Get items where content_regions is empty ([])."""
    return list(
        TrendItem.objects.filter(content_regions=[])
        .select_related('surface', 'region')
        .order_by('-hotness')[:limit]
    )


def run_heuristic_pass(items: list) -> tuple[int, list]:
    """
    Run heuristic classification on items.
    Returns (classified_count, remaining_items).
    """
    classified = 0
    remaining = []

    for item in items:
        regions, primary = classify_content_regions(
            lang_group=item.lang_group,
            platform=item.surface.platform if item.surface else '',
            surface_key=item.surface.key if item.surface else '',
            title=item.title_original or '',
        )

        if regions:
            item.content_regions = regions
            item.primary_region = primary
            item.save(update_fields=['content_regions', 'primary_region'])
            classified += 1
        else:
            remaining.append(item)

    return classified, remaining


def _get_llm_hot_percent() -> int:
    """Get the hotness percent threshold for LLM classification from SystemSettings."""
    try:
        from crawler_admin.models import SystemSettings
        return SystemSettings.get_setting('translation_hot_percent', default=10)
    except Exception:
        return 10


def _filter_top_percent(items: list, percent: int) -> list:
    """Keep only the top N% by hotness. Items are already sorted by -hotness."""
    if not items or percent >= 100:
        return items
    cutoff = max(1, len(items) * percent // 100)
    kept = items[:cutoff]
    logger.info(f"LLM top {percent}%: {cutoff}/{len(items)} items qualify")
    return kept


def run_llm_pass(items: list) -> int:
    """
    Run LLM classification on remaining items (top N% by hotness only).
    Returns classified_count.
    """
    if not ENABLE_LLM:
        logger.info("LLM classification disabled (ENABLE_REGION_LLM=false)")
        return 0

    # Only classify top N% by hotness to save LLM cost
    hot_percent = _get_llm_hot_percent()
    items = _filter_top_percent(items, hot_percent)

    classified = 0

    # Process in batches
    for i in range(0, len(items), BATCH_SIZE):
        batch = items[i:i + BATCH_SIZE]

        # Prepare batch for LLM
        batch_dicts = [
            {'title': item.title_original or '', 'platform': item.surface.platform if item.surface else ''}
            for item in batch
        ]

        results = classify_batch_llm(batch_dicts)

        for item, (regions, primary) in zip(batch, results):
            if regions:
                item.content_regions = regions
                item.primary_region = primary
                item.save(update_fields=['content_regions', 'primary_region'])
                classified += 1
            # If still empty, leave as [] — will be retried next cycle
            # (but likely stays empty — truly global/unclassifiable content)

    return classified


def run_once():
    """Run one classification cycle."""
    items = get_unclassified_items()
    if not items:
        logger.debug("No unclassified items found")
        return

    logger.info(f"Found {len(items)} unclassified items")

    # Heuristic pass
    heuristic_count, remaining = run_heuristic_pass(items)
    logger.info(f"Heuristic: classified {heuristic_count}, remaining {len(remaining)}")

    # LLM pass for remaining
    if remaining:
        llm_count = run_llm_pass(remaining)
        logger.info(f"LLM: classified {llm_count}, still unclassified {len(remaining) - llm_count}")


def run_worker_loop():
    """Main worker loop."""
    logger.info("Region classifier worker started")
    logger.info(f"POLL_INTERVAL={POLL_INTERVAL}s, ENABLE_LLM={ENABLE_LLM}")

    while True:
        try:
            run_once()
        except Exception as e:
            logger.error(f"Classification cycle error: {e}", exc_info=True)

        time.sleep(POLL_INTERVAL)


def main():
    try:
        run_worker_loop()
    except KeyboardInterrupt:
        logger.info("Region classifier worker stopped by user")
    except Exception as e:
        logger.error(f"Fatal worker error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
