"""
Celery tasks for crawler administration.

Tasks:
- flush_simulation_metrics_to_db: Periodic task to flush Redis metrics to database
"""
import asyncio
import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def flush_simulation_metrics_to_db():
    """
    Flush Redis metrics to DB every 5 minutes.

    This task runs periodically (configured in Celery beat schedule) to:
    1. Scan Redis for crawler metrics keys
    2. Aggregate counts by domain and hour bucket
    3. Atomically increment DB counters using F() expressions
    4. Delete Redis keys after successful flush

    Redis keys pattern: crawler:metrics:{domain}:{YYYY-MM-DD-HH}:{counter}
    DB model: DomainMetrics (domain, hour_bucket, direct_requests, simulated_requests, etc.)

    Schedule: Every 5 minutes (300 seconds)
    """
    from shared.metrics import flush_redis_to_db

    logger.info("Starting periodic flush of simulation metrics to DB")

    try:
        # Run async flush function
        asyncio.run(flush_redis_to_db())
        logger.info("Successfully flushed simulation metrics to DB")
    except Exception as e:
        logger.error(f"Failed to flush simulation metrics: {e}", exc_info=True)
        raise
