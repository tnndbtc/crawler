"""
Region classifier worker — classifies TrendItems by content region.

Runs heuristic pass first (instant), then LLM pass for remaining items.
Polls every 10 minutes for unclassified items (content_regions = []).

Enable/disable via ENABLE_REGION_CLASSIFIER in .env.
"""

import gc
import os
import sys
import time
import logging

import django
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

from django.db import close_old_connections, reset_queries
from django.utils import timezone
from crawler_admin.models import SystemSettings, TrendItem
from shared.region_classifier import classify_content_regions
from shared.region_llm_classifier import classify_batch_llm, BATCH_SIZE

logger = logging.getLogger(__name__)

POLL_INTERVAL = int(os.getenv('REGION_CLASSIFIER_POLL_INTERVAL', '600'))  # 10 min


def _is_llm_enabled() -> bool:
    """Read LLM enable flag from SystemSetting (default True). Shared with topic classifier."""
    try:
        val = SystemSettings.get_setting('enable_llm_classification', default='true')
        return str(val).lower() not in ('false', '0', 'no')
    except Exception:
        return True


def get_unclassified_items(per_platform: int | None = None) -> list:
    """
    Fetch top N never-tried items per platform, ordered by hotness desc.

    Uses region_per_platform_limit SystemSetting (default 50) so every platform
    gets proportional coverage — prevents high-volume platforms from monopolising
    the region classification queue and starving niche platforms of LLM budget.
    """
    if per_platform is None:
        try:
            per_platform = int(
                SystemSettings.get_setting('region_per_platform_limit', default=50)
            )
        except Exception:
            per_platform = 50

    platforms = (
        TrendItem.objects
        .filter(content_regions=[], region_classified_at__isnull=True)
        .order_by()          # clear default ordering — otherwise Django appends
                             # "collected_at" to the DISTINCT SELECT, making every
                             # row unique and returning ~N items instead of ~27 platforms.
        .values_list('surface__platform', flat=True)
        .distinct()
    )

    result = []
    for platform in platforms:
        items = list(
            TrendItem.objects
            .filter(
                content_regions=[],
                region_classified_at__isnull=True,
                surface__platform=platform,
            )
            .select_related('surface', 'region')
            .order_by('-hotness')[:per_platform]
        )
        result.extend(items)

    result.sort(key=lambda x: x.hotness or 0, reverse=True)
    return result


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
            item.region_classification_method = 'heuristic'
            item.save(update_fields=['content_regions', 'primary_region', 'region_classified_at', 'region_classification_method'])
            classified += 1
        else:
            remaining.append(item)

    return classified, remaining


def _filter_top_k_per_platform(items: list) -> list:
    """
    Keep top K items per platform for the LLM pass.

    Uses region_llm_top_k_per_platform SystemSetting (default 20).
    Replaces the old per-locale percentage filter: per-platform top-K aligns
    with how story_engine windows items (per-platform top-K then global limit),
    ensuring LLM budget is distributed across platforms rather than dominated
    by high-volume platforms whose items structurally score higher.
    """
    if not items:
        return items

    from collections import defaultdict
    try:
        k = int(SystemSettings.get_setting('region_llm_top_k_per_platform', default=20))
    except Exception:
        k = 20

    by_platform: dict[str, list] = defaultdict(list)
    for item in items:
        platform = item.surface.platform if item.surface else 'unknown'
        by_platform[platform].append(item)

    kept = []
    for platform, group in by_platform.items():
        group.sort(key=lambda x: x.hotness or 0, reverse=True)
        kept.extend(group[:k])
        logger.info(
            f"  Region LLM top {k} for platform={platform}: "
            f"{min(k, len(group))}/{len(group)}"
        )
    return kept


def run_llm_pass(items: list) -> int:
    """
    Run LLM classification on remaining items.
    Only processes the top K items per platform to control LLM cost.
    Returns classified_count.
    """
    if not _is_llm_enabled():
        logger.info("LLM classification disabled (enable_llm_classification=false)")
        return 0

    # Only classify top K per platform to save LLM cost
    items = _filter_top_k_per_platform(items)
    logger.info(f"LLM total after per-platform filtering: {len(items)} items")

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
            item.region_classification_method = 'llm'
            if regions:
                item.content_regions = regions
                item.primary_region = primary
                item.save(update_fields=['content_regions', 'primary_region', 'region_classified_at', 'region_classification_method'])
                classified += 1
            else:
                # LLM said no specific region — mark as tried so we don't re-ask
                item.save(update_fields=['region_classified_at', 'region_classification_method'])

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

    # LLM pass for remaining items (only top K per platform will actually be sent)
    if _is_llm_enabled():
        llm_sent_items = _filter_top_k_per_platform(remaining)
        llm_sent_urls = {item.id for item in llm_sent_items}
        llm_count = 0
        if llm_sent_items:
            llm_count = run_llm_pass(remaining)  # run_llm_pass re-applies the filter
        logger.info(f"LLM: classified {llm_count}, sent {len(llm_sent_items)}, unclassified {len(remaining) - llm_count}")
    else:
        # LLM disabled — mark every remaining item as tried so they don't re-poll
        # (all items land in not_sent block below, which sets region_classification_method='skipped')
        logger.info(f"LLM disabled — marking {len(remaining)} remaining items as tried")
        llm_sent_urls = set()

    # Mark items NOT sent to LLM as tried too (they're low-hotness and won't be re-attempted
    # via LLM; heuristic already failed on them). This prevents infinite re-polling.
    now = timezone.now()
    not_sent = [item for item in remaining if item.id not in llm_sent_urls]
    for item in not_sent:
        item.region_classified_at = now
        item.region_classification_method = 'skipped'
        item.save(update_fields=['region_classified_at', 'region_classification_method'])
    if not_sent:
        logger.info(f"Marked {len(not_sent)} low-hotness items as tried (skip LLM)")


def run_worker_loop():
    """Main worker loop."""
    logger.info("Region classifier worker started")
    logger.info(f"POLL_INTERVAL={POLL_INTERVAL}s, enable_llm_classification={_is_llm_enabled()}")

    while True:
        try:
            run_once()
        except Exception as e:
            logger.error(f"Classification cycle error: {e}", exc_info=True)
        finally:
            close_old_connections()
            reset_queries()
            gc.collect()

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
