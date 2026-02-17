"""
Hotness worker - Computes and updates hotness scores for trend items.

Responsibilities:
1. Compute hotness for new items (hotness=NULL)
2. Recompute hotness for recent items (<48h old) where score may have changed significantly
3. Update hotness_computed_at timestamp

Hotness Formula:
    hotness = recency_decay × log10(1 + weighted_engagement) × 100

    where:
    - recency_decay = exp(-0.05 × hours_since_collected)
    - weighted_engagement = upvotes×1.0 + comments×2.0 + views×0.1 + shares×3.0 + (100-rank)×10

Properties:
- Time decay: Old content naturally loses hotness
- Log-scaled engagement: Prevents viral outliers from dominating
- Platform-agnostic: Works across Reddit, HN, YouTube, etc.
- Typical range: 0-1000

Configuration:
    HOTNESS_WORKER_POLL_INTERVAL: How often to run (default: 300s = 5 minutes)

Usage:
    python src/crawler_api/workers/hotness_worker.py
"""

import os
import sys
import asyncio
import logging
from datetime import datetime, timedelta
from typing import List

import django
from django.utils import timezone
from asgiref.sync import sync_to_async

# Setup Django
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

from crawler_admin.models import TrendItem
from shared.hotness import compute_hotness, should_recompute_hotness

logger = logging.getLogger(__name__)

# Configuration from environment
HOTNESS_WORKER_POLL_INTERVAL = int(os.getenv('HOTNESS_WORKER_POLL_INTERVAL', '300'))  # 5 minutes
BATCH_SIZE = int(os.getenv('HOTNESS_BATCH_SIZE', '100'))


async def compute_hotness_for_new_items(batch_size: int = 100) -> int:
    """
    Compute hotness for items that don't have a score yet.

    Targets items where:
    - hotness is NULL
    - item has required fields (collected_at, engagement_signals)

    Args:
        batch_size: Number of items to process per batch

    Returns:
        Number of items processed
    """
    # Find items without hotness
    items = await sync_to_async(list)(
        TrendItem.objects.filter(
            hotness__isnull=True
        ).select_related('region', 'surface')[:batch_size]
    )

    if not items:
        logger.debug("No new items need hotness computation")
        return 0

    logger.info(f"Computing hotness for {len(items)} new item(s)")

    processed_count = 0
    for item in items:
        try:
            # Compute hotness
            hotness_score = compute_hotness(item)

            # Update item
            item.hotness = hotness_score
            item.hotness_computed_at = timezone.now()
            await sync_to_async(item.save)(
                update_fields=['hotness', 'hotness_computed_at']
            )

            logger.debug(
                f"✅ Computed hotness={hotness_score:.2f} for new item #{item.id} "
                f"({item.lang_group})"
            )
            processed_count += 1

        except Exception as e:
            logger.error(
                f"Failed to compute hotness for item #{item.id}: {e}",
                exc_info=True
            )
            # Continue processing other items

    if processed_count > 0:
        logger.info(f"Computed hotness for {processed_count} new item(s)")

    return processed_count


async def recompute_hotness_for_recent_items(batch_size: int = 100) -> int:
    """
    Recompute hotness for recent items where score may have changed.

    Targets items where:
    - Item is <48h old (still actively changing)
    - Hotness was computed >6h ago (stale)
    - OR hotness is NULL but item has collected_at

    Args:
        batch_size: Number of items to process per batch

    Returns:
        Number of items recomputed
    """
    # Find recent items (collected in last 48 hours)
    cutoff_time = timezone.now() - timedelta(hours=48)

    # Items that need recomputation:
    # 1. Recent items with stale hotness (computed >6h ago)
    # 2. Recent items with NULL hotness
    stale_cutoff = timezone.now() - timedelta(hours=6)

    items = await sync_to_async(list)(
        TrendItem.objects.filter(
            collected_at__gte=cutoff_time
        ).filter(
            django.db.models.Q(hotness_computed_at__lt=stale_cutoff) |
            django.db.models.Q(hotness_computed_at__isnull=True)
        ).select_related('region', 'surface')[:batch_size]
    )

    if not items:
        logger.debug("No recent items need hotness recomputation")
        return 0

    logger.info(f"Recomputing hotness for {len(items)} recent item(s)")

    recomputed_count = 0
    for item in items:
        try:
            # Check if recomputation is needed (additional validation)
            if not should_recompute_hotness(item, threshold_hours=6.0):
                continue

            # Compute new hotness
            old_hotness = item.hotness
            new_hotness = compute_hotness(item)

            # Update item
            item.hotness = new_hotness
            item.hotness_computed_at = timezone.now()
            await sync_to_async(item.save)(
                update_fields=['hotness', 'hotness_computed_at']
            )

            change = new_hotness - (old_hotness or 0.0)
            logger.debug(
                f"♻️  Recomputed hotness for item #{item.id}: "
                f"{old_hotness:.2f} → {new_hotness:.2f} "
                f"(change: {change:+.2f})"
            )
            recomputed_count += 1

        except Exception as e:
            logger.error(
                f"Failed to recompute hotness for item #{item.id}: {e}",
                exc_info=True
            )
            # Continue processing other items

    if recomputed_count > 0:
        logger.info(f"Recomputed hotness for {recomputed_count} recent item(s)")

    return recomputed_count


async def backfill_language_classification(batch_size: int = 100) -> int:
    """
    Backfill language classification for items missing base_lang/lang_group.

    This is a migration helper for existing items created before language-aware system.

    Args:
        batch_size: Number of items to process per batch

    Returns:
        Number of items classified
    """
    from shared.language_detection import classify_item_language

    # Find items without language classification
    items = await sync_to_async(list)(
        TrendItem.objects.filter(
            base_lang__isnull=True
        ).select_related('region', 'surface')[:batch_size]
    )

    if not items:
        logger.debug("No items need language classification backfill")
        return 0

    logger.info(f"Backfilling language classification for {len(items)} item(s)")

    classified_count = 0
    for item in items:
        try:
            # Classify language
            base_lang, locale, lang_group = classify_item_language(
                title=item.title_original,
                description=item.description_original,
                region_default_locale=item.region.default_locale
            )

            # Update item
            item.base_lang = base_lang
            item.locale = locale
            item.lang_group = lang_group
            item.lang_detected_at = timezone.now()
            await sync_to_async(item.save)(
                update_fields=['base_lang', 'locale', 'lang_group', 'lang_detected_at']
            )

            logger.debug(
                f"🌍 Classified item #{item.id}: "
                f"base_lang={base_lang}, lang_group={lang_group}"
            )
            classified_count += 1

        except Exception as e:
            logger.error(
                f"Failed to classify language for item #{item.id}: {e}",
                exc_info=True
            )

    if classified_count > 0:
        logger.info(f"Classified {classified_count} item(s)")

    return classified_count


async def get_hotness_stats() -> dict:
    """
    Get hotness computation statistics for monitoring.

    Returns:
        Dict with stats:
        {
            'total_items': 1000,
            'items_with_hotness': 950,
            'coverage_pct': 95.0,
            'items_need_backfill': 50,
            'items_need_recompute': 20
        }
    """
    cutoff_time = timezone.now() - timedelta(hours=48)
    stale_cutoff = timezone.now() - timedelta(hours=6)

    total = await sync_to_async(TrendItem.objects.count)()
    with_hotness = await sync_to_async(
        TrendItem.objects.filter(hotness__isnull=False).count
    )()

    need_backfill = await sync_to_async(
        TrendItem.objects.filter(hotness__isnull=True).count
    )()

    need_recompute = await sync_to_async(
        TrendItem.objects.filter(
            collected_at__gte=cutoff_time,
            hotness_computed_at__lt=stale_cutoff
        ).count
    )()

    coverage_pct = (with_hotness / total * 100.0) if total > 0 else 0.0

    return {
        'total_items': total,
        'items_with_hotness': with_hotness,
        'coverage_pct': round(coverage_pct, 2),
        'items_need_backfill': need_backfill,
        'items_need_recompute': need_recompute
    }


async def run_worker_loop():
    """
    Main worker loop.

    Periodically:
    1. Backfill language classification for old items
    2. Compute hotness for new items (NULL hotness)
    3. Recompute hotness for recent items (<48h old)
    4. Log stats
    5. Sleep and repeat

    Never crashes - all errors are logged and handled gracefully.
    """
    logger.info("Hotness worker started")
    logger.info(f"HOTNESS_WORKER_POLL_INTERVAL: {HOTNESS_WORKER_POLL_INTERVAL}s")
    logger.info(f"BATCH_SIZE: {BATCH_SIZE}")

    while True:
        try:
            cycle_start = timezone.now()

            # 1. Backfill language classification (for migration)
            classified = await backfill_language_classification(BATCH_SIZE)

            # 2. Compute hotness for new items
            new_computed = await compute_hotness_for_new_items(BATCH_SIZE)

            # 3. Recompute hotness for recent items
            recomputed = await recompute_hotness_for_recent_items(BATCH_SIZE)

            # 4. Get stats
            stats = await get_hotness_stats()

            # Log cycle summary
            total_processed = classified + new_computed + recomputed
            if total_processed > 0:
                logger.info(
                    f"Cycle complete: "
                    f"classified={classified}, "
                    f"new_computed={new_computed}, "
                    f"recomputed={recomputed} | "
                    f"Coverage: {stats['coverage_pct']:.1f}% "
                    f"({stats['items_with_hotness']}/{stats['total_items']})"
                )
            else:
                logger.debug(
                    f"Cycle complete (no work): "
                    f"Coverage: {stats['coverage_pct']:.1f}%"
                )

            # Log if there's a backlog
            if stats['items_need_backfill'] > BATCH_SIZE:
                logger.warning(
                    f"⚠️  Large backlog: {stats['items_need_backfill']} items "
                    f"need hotness computation"
                )

        except Exception as e:
            # Never crash the worker loop
            logger.error(f"Worker loop error: {e}", exc_info=True)

        # Sleep before next poll
        await asyncio.sleep(HOTNESS_WORKER_POLL_INTERVAL)


def main():
    """Entry point for hotness worker."""
    try:
        asyncio.run(run_worker_loop())
    except KeyboardInterrupt:
        logger.info("Hotness worker stopped by user")
    except Exception as e:
        logger.error(f"Fatal worker error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
