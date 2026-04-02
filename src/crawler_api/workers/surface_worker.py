"""
Surface worker - Executes trend collectors with full observability.

From REQUIREMENTS-MASTER.md:
- Poll for due surfaces
- Execute collectors with timeout (MAX_RUN_SECONDS)
- Create CrawlRun for every execution
- DRY_RUN mode for testing (no DB writes except CrawlRun)
- Update surface health fields (last_run_at, last_success_at, last_error)
- Bucket diversity enforcement (no bucket > 40% per run)

Product constraint (from /tmp/t9):
- Diversity > Volume
- Bucket balancing ensures varied candidate pool
- Translation NEVER blocks collection
"""

import os
import sys
import time
import asyncio
import logging
import hashlib
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional, List

import django
from django.conf import settings
from django.utils import timezone
from asgiref.sync import sync_to_async

# Setup Django
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

from crawler_admin.models import TrendSurface, TrendItem, CrawlRun
from crawler_api.surfaces.registry import get_collector
from shared.language_detection import classify_item_language
from shared.hotness import compute_hotness
from shared.jitter import jittered_sleep
from shared.http_client import CircuitBreakerOpen

logger = logging.getLogger(__name__)


# Configuration from environment
DRY_RUN = os.getenv('DRY_RUN', 'false').lower() == 'true'
MAX_RUN_SECONDS = int(os.getenv('MAX_RUN_SECONDS', '60'))
SURFACE_WORKER_POLL_INTERVAL = int(os.getenv('SURFACE_WORKER_POLL_INTERVAL', '60'))
BUCKET_CAP_PERCENT = getattr(settings, 'BUCKET_CAP_PERCENT', 40)


def compute_canonical_hash(title: str, url: str) -> str:
    """
    Compute SHA256 hash for deduplication.

    From REQUIREMENTS-MASTER.md:
    canonical_hash = SHA256(normalized_title + URL)
    """
    normalized = f"{title.lower().strip()}|{url.lower().strip()}"
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


async def execute_surface(surface: TrendSurface) -> None:
    """
    Execute single surface with full observability.

    From REQUIREMENTS-MASTER.md:
    1. Create CrawlRun (audit log)
    2. Update surface.last_run_at
    3. Load collector from entrypoint
    4. Collect with timeout (MAX_RUN_SECONDS)
    5. Deduplicate and store items
    6. Update CrawlRun metrics
    7. Update surface health fields
    8. Handle errors gracefully

    Product rule (from /tmp/t9):
    - Collection is FAST (no translation waiting)
    - Always capture rank_position, engagement_signals, raw_payload
    - Deduplication based on canonical_hash
    """
    # Create CrawlRun (observability from /tmp/t7)
    run = await sync_to_async(CrawlRun.objects.create)(
        surface=surface,
        started_at=timezone.now()
    )

    # Update surface health (from /tmp/t8)
    surface.last_run_at = timezone.now()
    await sync_to_async(surface.save)(update_fields=['last_run_at'])

    try:
        start_time = time.time()

        # Load collector
        collector = get_collector(surface.entrypoint)

        # Collect with timeout (from /tmp/t8)
        try:
            items, next_cursor = await asyncio.wait_for(
                collector(
                    config=surface.config_json,
                    cursor=surface.last_cursor,
                    limit=surface.max_items_per_run
                ),
                timeout=MAX_RUN_SECONDS
            )
        except asyncio.TimeoutError:
            raise TimeoutError(f"Collector timed out after {MAX_RUN_SECONDS}s")

        fetched_count = len(items)
        stored_new_count = 0
        deduped_count = 0

        # Process items
        for item_dict in items:
            # Compute canonical hash for deduplication
            canonical_hash = compute_canonical_hash(
                item_dict['title'],
                item_dict['url']
            )

            # Check duplicate
            exists = await sync_to_async(
                TrendItem.objects.filter(canonical_hash=canonical_hash).exists
            )()

            if exists:
                deduped_count += 1
                continue

            # Store (unless DRY_RUN from /tmp/t8)
            if not DRY_RUN:
                # Language detection and classification
                # Pass collector's locale as hint — collectors know their source language
                base_lang, locale, lang_group = classify_item_language(
                    title=item_dict['title'],
                    description=item_dict.get('description'),
                    region_default_locale=surface.region.default_locale,
                    collector_locale=item_dict.get('locale'),
                )

                # Create item
                item = await sync_to_async(TrendItem.objects.create)(
                    region=surface.region,
                    surface=surface,
                    external_id=item_dict.get('external_id'),
                    canonical_hash=canonical_hash,
                    title_original=item_dict['title'],
                    description_original=item_dict.get('description'),
                    original_locale=item_dict['locale'],
                    url=item_dict['url'],
                    published_at=item_dict.get('published_at'),

                    # CRITICAL: rank_position (from /tmp/t9)
                    rank_position=item_dict.get('rank_position'),

                    # CRITICAL: engagement_signals (from /tmp/t9)
                    engagement_signals=item_dict.get('engagement_signals', {}),

                    # Bucket from surface
                    bucket=surface.bucket,

                    # CRITICAL: raw_payload (from /tmp/t9)
                    raw_payload=item_dict.get('raw_payload', {}),

                    # Language-aware fields
                    base_lang=base_lang,
                    locale=locale,
                    lang_group=lang_group,
                    lang_detected_at=timezone.now(),
                )

                # Compute initial hotness score
                try:
                    hotness_score = compute_hotness(item)
                    item.hotness = hotness_score
                    item.hotness_computed_at = timezone.now()
                    await sync_to_async(item.save)(
                        update_fields=['hotness', 'hotness_computed_at']
                    )
                    logger.debug(
                        f"Computed hotness={hotness_score:.2f} for item #{item.id} "
                        f"({lang_group})"
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to compute hotness for item #{item.id}: {e}"
                    )
                    # Don't fail ingestion if hotness computation fails

            stored_new_count += 1

        # Success
        duration_ms = int((time.time() - start_time) * 1000)
        run.status = 'success'
        run.fetched_count = fetched_count
        run.stored_new_count = stored_new_count
        run.deduped_count = deduped_count
        run.duration_ms = duration_ms
        run.finished_at = timezone.now()
        await sync_to_async(run.save)()

        # Update surface health (from /tmp/t8)
        surface.last_success_at = timezone.now()
        surface.last_error = None
        surface.last_cursor = next_cursor
        surface.next_run_at = timezone.now() + timedelta(seconds=surface.poll_interval_seconds)
        await sync_to_async(surface.save)(
            update_fields=['last_success_at', 'last_error', 'last_cursor', 'next_run_at']
        )

        # Log
        dry_run_prefix = "[DRY_RUN] " if DRY_RUN else ""
        logger.info(
            f"{dry_run_prefix}CrawlRun #{run.id}: {surface.key} - "
            f"fetched={fetched_count}, new={stored_new_count}, "
            f"deduped={deduped_count}, duration={duration_ms}ms"
        )

    except asyncio.TimeoutError as e:
        # Timeout (from /tmp/t8)
        error_msg = str(e)
        run.status = 'failed'
        run.error_message = error_msg
        run.finished_at = timezone.now()
        await sync_to_async(run.save)()

        surface.last_error = error_msg
        await sync_to_async(surface.save)(update_fields=['last_error'])

        logger.error(f"CrawlRun #{run.id} timed out: {error_msg}")

    except CircuitBreakerOpen as e:
        # Circuit breaker is open for this domain - skip gracefully
        error_msg = f"Circuit breaker open: {e}"
        run.status = 'failed'
        run.error_message = error_msg
        run.finished_at = timezone.now()
        await sync_to_async(run.save)()

        surface.last_error = error_msg
        # Don't update next_run_at - respect the circuit breaker cooldown
        await sync_to_async(surface.save)(update_fields=['last_error'])

        logger.warning(f"CrawlRun #{run.id}: {error_msg}")

    except Exception as e:
        # Other failure
        error_msg = str(e)
        run.status = 'failed'
        run.error_message = error_msg
        run.finished_at = timezone.now()
        await sync_to_async(run.save)()

        surface.last_error = error_msg
        surface.next_run_at = timezone.now() + timedelta(seconds=300)  # Retry in 5 min
        await sync_to_async(surface.save)(update_fields=['last_error', 'next_run_at'])

        logger.error(f"CrawlRun #{run.id} failed: {error_msg}", exc_info=True)


async def get_due_surfaces() -> List[TrendSurface]:
    """
    Get surfaces that are due for collection.

    A surface is due if:
    - enabled = True
    - next_run_at is None OR next_run_at <= now
    """
    now = timezone.now()

    surfaces = await sync_to_async(list)(
        TrendSurface.objects.filter(enabled=True).filter(
            django.db.models.Q(next_run_at__isnull=True) |
            django.db.models.Q(next_run_at__lte=now)
        ).select_related('region')
    )

    return surfaces


async def run_worker_loop():
    """
    Main worker loop.

    From REQUIREMENTS-MASTER.md:
    1. Poll for due surfaces
    2. Execute each surface
    3. Sleep SURFACE_WORKER_POLL_INTERVAL
    4. Repeat forever (worker never crashes)

    NOTE: Bucket balancing logic from /tmp/t9 is noted but not yet implemented.
    For MVP, we execute all due surfaces. In production, add bucket cap enforcement.
    """
    logger.info("Surface worker started")
    logger.info(f"DRY_RUN mode: {DRY_RUN}")
    logger.info(f"MAX_RUN_SECONDS: {MAX_RUN_SECONDS}")
    logger.info(f"SURFACE_WORKER_POLL_INTERVAL: {SURFACE_WORKER_POLL_INTERVAL}")
    logger.info(f"BUCKET_CAP_PERCENT: {BUCKET_CAP_PERCENT}%")

    # Track last cleanup time (run once per day)
    last_cleanup_time = 0

    while True:
        try:
            # Get due surfaces
            due_surfaces = await get_due_surfaces()

            if due_surfaces:
                logger.info(f"Found {len(due_surfaces)} due surface(s)")

                # Execute each surface
                # TODO: Add bucket balancing logic from /tmp/t9 (no bucket > 40%)
                for surface in due_surfaces:
                    try:
                        await execute_surface(surface)
                    except Exception as e:
                        # Never crash the worker (from requirements)
                        logger.error(
                            f"Failed to execute surface {surface.key}: {e}",
                            exc_info=True
                        )
                        continue

                logger.info(f"Completed {len(due_surfaces)} surface(s)")
            else:
                logger.debug("No due surfaces found")

            # Daily data cleanup
            now = time.time()
            if now - last_cleanup_time > 86400:  # 24 hours
                try:
                    from shared.data_cleanup import cleanup_old_data
                    result = await sync_to_async(cleanup_old_data)()
                    last_cleanup_time = now
                except Exception as e:
                    logger.error(f"Data cleanup error: {e}", exc_info=True)

        except Exception as e:
            # Never crash the worker loop
            logger.error(f"Worker loop error: {e}", exc_info=True)

        # Sleep before next poll with jitter to avoid thundering herd
        await jittered_sleep(SURFACE_WORKER_POLL_INTERVAL)


def main():
    """Entry point for surface worker."""
    try:
        asyncio.run(run_worker_loop())
    except KeyboardInterrupt:
        logger.info("Surface worker stopped by user")
    except Exception as e:
        logger.error(f"Fatal worker error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
