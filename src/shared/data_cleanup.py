"""
Data cleanup — deletes crawled data older than the configured retention period.

Reads `data_retention_days` from SystemSettings:
  - 30 = keep 30 days, delete older
  - -1 = keep forever, never delete

Called once per day from the surface worker.
"""

import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)


def cleanup_old_data():
    """
    Delete TrendItems, ItemDerivations, and CrawlRuns older than retention period.

    Returns:
        Dict with counts of deleted records, or None if cleanup was skipped.
    """
    from crawler_admin.models import SystemSettings, TrendItem, CrawlRun, ItemDerivation

    retention_days = SystemSettings.get_setting('data_retention_days', default=-1)

    if retention_days < 0:
        logger.debug("Data retention set to -1 (keep forever), skipping cleanup")
        return None

    cutoff = timezone.now() - timedelta(days=retention_days)

    logger.info(f"Data cleanup: deleting records older than {retention_days} days (before {cutoff})")

    # Delete ItemDerivations for old items (cascade would handle this,
    # but explicit delete gives us a count)
    derivations_deleted, _ = ItemDerivation.objects.filter(
        item__collected_at__lt=cutoff
    ).delete()

    # Delete old TrendItems (this is the big one)
    items_deleted, _ = TrendItem.objects.filter(
        collected_at__lt=cutoff
    ).delete()

    # Delete old CrawlRuns
    runs_deleted, _ = CrawlRun.objects.filter(
        created_at__lt=cutoff
    ).delete()

    result = {
        'items': items_deleted,
        'derivations': derivations_deleted,
        'crawl_runs': runs_deleted,
        'cutoff': cutoff.isoformat(),
    }

    total = items_deleted + derivations_deleted + runs_deleted
    if total > 0:
        logger.info(
            f"Data cleanup complete: deleted {items_deleted} items, "
            f"{derivations_deleted} derivations, {runs_deleted} crawl runs"
        )
    else:
        logger.debug("Data cleanup: nothing to delete")

    return result
