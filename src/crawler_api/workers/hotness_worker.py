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

import gc
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

from django.db import close_old_connections, reset_queries
from crawler_admin.models import TrendItem, SystemSettings
from shared.hotness import compute_hotness, should_recompute_hotness

logger = logging.getLogger(__name__)

# Configuration from environment
HOTNESS_WORKER_POLL_INTERVAL = int(os.getenv('HOTNESS_WORKER_POLL_INTERVAL', '300'))  # 5 minutes
BATCH_SIZE = int(os.getenv('HOTNESS_BATCH_SIZE', '100'))
BACKFILL_MODE = os.getenv('HOTNESS_BACKFILL_MODE', 'normal')  # 'normal' or 'aggressive'


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
            'hotness_coverage_pct': 95.0,
            'items_need_hotness_backfill': 50,
            'items_need_recompute': 20,
            'items_with_base_lang': 900,
            'lang_coverage_pct': 90.0,
            'items_need_lang_backfill': 100
        }
    """
    cutoff_time = timezone.now() - timedelta(hours=48)
    stale_cutoff = timezone.now() - timedelta(hours=6)

    total = await sync_to_async(TrendItem.objects.count)()
    with_hotness = await sync_to_async(
        TrendItem.objects.filter(hotness__isnull=False).count
    )()

    need_hotness_backfill = await sync_to_async(
        TrendItem.objects.filter(hotness__isnull=True).count
    )()

    need_recompute = await sync_to_async(
        TrendItem.objects.filter(
            collected_at__gte=cutoff_time,
            hotness_computed_at__lt=stale_cutoff
        ).count
    )()

    # Language coverage stats
    with_base_lang = await sync_to_async(
        TrendItem.objects.filter(base_lang__isnull=False).count
    )()

    need_lang_backfill = await sync_to_async(
        TrendItem.objects.filter(base_lang__isnull=True).count
    )()

    hotness_coverage_pct = (with_hotness / total * 100.0) if total > 0 else 0.0
    lang_coverage_pct = (with_base_lang / total * 100.0) if total > 0 else 0.0

    return {
        'total_items': total,
        'items_with_hotness': with_hotness,
        'hotness_coverage_pct': round(hotness_coverage_pct, 2),
        'items_need_hotness_backfill': need_hotness_backfill,
        'items_need_recompute': need_recompute,
        'items_with_base_lang': with_base_lang,
        'lang_coverage_pct': round(lang_coverage_pct, 2),
        'items_need_lang_backfill': need_lang_backfill
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

    In aggressive mode (HOTNESS_BACKFILL_MODE=aggressive):
    - Processes larger batches (500 items)
    - Loops until backlog < 100 items
    - For rapid initial backfill

    Never crashes - all errors are logged and handled gracefully.
    """
    logger.info("Hotness worker started")
    logger.info(f"HOTNESS_WORKER_POLL_INTERVAL: {HOTNESS_WORKER_POLL_INTERVAL}s")
    logger.info(f"BATCH_SIZE: {BATCH_SIZE}")
    logger.info(f"BACKFILL_MODE: {BACKFILL_MODE}")

    while True:
        try:
            cycle_start = timezone.now()

            # Check if hotness worker is enabled
            if not SystemSettings.get_setting("crawler_hotness_worker_enabled", default=False):
                logger.info("Hotness worker disabled by configuration")
                await asyncio.sleep(HOTNESS_WORKER_POLL_INTERVAL)
                continue

            # Determine batch size based on mode
            if BACKFILL_MODE == 'aggressive':
                batch_size = 500
                max_iterations = 50  # Prevent infinite loops
            else:
                batch_size = BATCH_SIZE
                max_iterations = 1

            # 1. Backfill language classification (for migration)
            total_classified = 0
            for iteration in range(max_iterations):
                classified = await backfill_language_classification(batch_size)
                total_classified += classified

                # In aggressive mode, loop until backlog < 100
                if BACKFILL_MODE == 'aggressive':
                    # Release ORM objects between aggressive-mode inner iterations —
                    # same fix as region/topic classifier workers (2026-04-17 OOM kill).
                    await sync_to_async(close_old_connections)()
                    await sync_to_async(reset_queries)()
                    gc.collect()

                    stats = await get_hotness_stats()
                    remaining = stats['items_need_lang_backfill']

                    if classified > 0:
                        pct_complete = stats['lang_coverage_pct']
                        logger.info(
                            f"Language backfill: {total_classified} processed this cycle, "
                            f"{remaining} remaining ({pct_complete:.1f}% complete)"
                        )

                    if remaining < 100:
                        logger.info(f"Language backfill complete: {remaining} items remaining")
                        break

                    if classified == 0:
                        # No more items to process
                        break
                else:
                    # Normal mode - single batch
                    break

            # 2. Compute hotness for new items
            total_computed = 0
            for iteration in range(max_iterations):
                new_computed = await compute_hotness_for_new_items(batch_size)
                total_computed += new_computed

                # In aggressive mode, loop until backlog < 100
                if BACKFILL_MODE == 'aggressive':
                    # Release ORM objects between aggressive-mode inner iterations.
                    await sync_to_async(close_old_connections)()
                    await sync_to_async(reset_queries)()
                    gc.collect()

                    stats = await get_hotness_stats()
                    remaining = stats['items_need_hotness_backfill']

                    if new_computed > 0:
                        pct_complete = stats['hotness_coverage_pct']
                        logger.info(
                            f"Hotness backfill: {total_computed} processed this cycle, "
                            f"{remaining} remaining ({pct_complete:.1f}% complete)"
                        )

                    if remaining < 100:
                        logger.info(f"Hotness backfill complete: {remaining} items remaining")
                        break

                    if new_computed == 0:
                        # No more items to process
                        break
                else:
                    # Normal mode - single batch
                    break

            # 3. Recompute hotness for recent items (always single batch)
            recomputed = await recompute_hotness_for_recent_items(BATCH_SIZE)

            # 4. Get final stats
            stats = await get_hotness_stats()

            # Log cycle summary with both language and hotness coverage
            total_processed = total_classified + total_computed + recomputed
            if total_processed > 0:
                logger.info(
                    f"Cycle complete: "
                    f"classified={total_classified}, "
                    f"new_computed={total_computed}, "
                    f"recomputed={recomputed}"
                )
                logger.info(
                    f"Coverage: "
                    f"lang={stats['lang_coverage_pct']:.1f}% ({stats['items_with_base_lang']}/{stats['total_items']}), "
                    f"hotness={stats['hotness_coverage_pct']:.1f}% ({stats['items_with_hotness']}/{stats['total_items']})"
                )
            else:
                logger.debug(
                    f"Cycle complete (no work): "
                    f"lang={stats['lang_coverage_pct']:.1f}%, "
                    f"hotness={stats['hotness_coverage_pct']:.1f}%"
                )

            # Log if there's a backlog
            if stats['items_need_lang_backfill'] > BATCH_SIZE:
                logger.warning(
                    f"⚠️  Language backlog: {stats['items_need_lang_backfill']} items "
                    f"need classification"
                )
            if stats['items_need_hotness_backfill'] > BATCH_SIZE:
                logger.warning(
                    f"⚠️  Hotness backlog: {stats['items_need_hotness_backfill']} items "
                    f"need computation"
                )

        except Exception as e:
            # Never crash the worker loop
            logger.error(f"Worker loop error: {e}", exc_info=True)
        finally:
            # Release Django ORM objects and DB connection state — same fix as
            # region/topic classifier workers (2026-04-17 OOM kill).
            await sync_to_async(close_old_connections)()
            await sync_to_async(reset_queries)()
            gc.collect()

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
