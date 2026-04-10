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
    """
    Get items where classification was never attempted.
    Excludes items the LLM already checked and returned 'no region'.
    """
    return list(
        TrendItem.objects.filter(
            content_regions=[],
            region_classified_at__isnull=True,
        )
        .select_related('surface', 'region')
        .order_by('-hotness')[:limit]
    )


def run_heuristic_pass(items: list) -> tuple[int, list]:
    """
    Run heuristic classification on items.
    Returns (classified_count, remaining_items).
    Items that heuristic couldn't classify are passed to LLM pass
    (they'll get region_classified_at set there).
    """
    classified = 0
    remaining = []
    now = timezone.now()

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
            item.region_classified_at = now
            item.save(update_fields=['content_regions', 'primary_region', 'region_classified_at'])
            classified += 1
        else:
            remaining.append(item)

    return classified, remaining


def _get_locale_hot_percent(lang_group: str | None) -> int:
    """
    Get the per-locale hotness percent threshold for LLM classification.
    Reads translation_hot_percent_<lang_group> from SystemSettings.
    Falls back to translation_hot_percent (global) then to 10.
    """
    try:
        from crawler_admin.models import SystemSettings
        if lang_group:
            key = f'translation_hot_percent_{lang_group}'
            val = SystemSettings.get_setting(key, default=None)
            if val is not None:
                return int(val)
        # Fallback to global
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

    # Group items by lang_group (preserving hotness order within each group)
    by_locale: dict[str, list] = {}
    for item in items:
        lg = item.lang_group or 'unknown'
        by_locale.setdefault(lg, []).append(item)

    # For each locale, keep only top N%
    kept = []
    for lg, group in by_locale.items():
        percent = _get_locale_hot_percent(lg)
        cutoff = max(1, len(group) * percent // 100)
        kept.extend(group[:cutoff])
        logger.info(f"  LLM top {percent}% for lang={lg}: {cutoff}/{len(group)}")

    return kept


def run_llm_pass(items: list) -> int:
    """
    Run LLM classification on remaining items.
    Only processes items in the top N% by hotness WITHIN each locale,
    using translation_hot_percent_<locale> per-locale settings.
    Returns classified_count.
    """
    if not ENABLE_LLM:
        logger.info("LLM classification disabled (ENABLE_REGION_LLM=false)")
        return 0

    # Only classify top N% by hotness (per locale) to save LLM cost
    items = _filter_top_percent_per_locale(items)
    logger.info(f"LLM total after per-locale filtering: {len(items)} items")

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

        now = timezone.now()
        for item, (regions, primary) in zip(batch, results):
            # Always set region_classified_at so we don't re-process this item
            item.region_classified_at = now
            if regions:
                item.content_regions = regions
                item.primary_region = primary
                item.save(update_fields=['content_regions', 'primary_region', 'region_classified_at'])
                classified += 1
            else:
                # LLM said no specific region — mark as tried so we don't re-ask
                item.save(update_fields=['region_classified_at'])

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

    if not remaining:
        return

    # LLM pass for remaining items (only top N% per locale will actually be sent)
    llm_sent_items = _filter_top_percent_per_locale(remaining)
    llm_sent_urls = {item.id for item in llm_sent_items}
    llm_count = 0
    if llm_sent_items:
        llm_count = run_llm_pass(remaining)  # run_llm_pass re-applies the filter
    logger.info(f"LLM: classified {llm_count}, sent {len(llm_sent_items)}, unclassified {len(remaining) - llm_count}")

    # Mark items NOT sent to LLM as tried too (they're low-hotness and won't be re-attempted
    # via LLM; heuristic already failed on them). This prevents infinite re-polling.
    now = timezone.now()
    not_sent = [item for item in remaining if item.id not in llm_sent_urls]
    for item in not_sent:
        item.region_classified_at = now
        item.save(update_fields=['region_classified_at'])
    if not_sent:
        logger.info(f"Marked {len(not_sent)} low-hotness items as tried (skip LLM)")


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
