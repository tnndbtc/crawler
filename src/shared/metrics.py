"""
Redis-based metrics system for crawler human behavior simulation.

Tracks counters per domain per hour:
- direct_requests: Normal crawler requests
- simulated_requests: Human-like behavior requests
- fallbacks: Failed simulation attempts
- budget_blocked: Requests blocked by budget enforcement

Counters are stored in Redis and flushed to DB every 5 minutes.
"""
import redis.asyncio as redis
from django.utils import timezone
from datetime import datetime
from asgiref.sync import sync_to_async
import logging

logger = logging.getLogger(__name__)

_redis_client = None


async def get_redis_client():
    """Get or create Redis client."""
    global _redis_client
    if _redis_client is None:
        from django.conf import settings
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


def get_hour_bucket() -> str:
    """Get current hour bucket string: YYYY-MM-DD-HH."""
    return timezone.now().strftime('%Y-%m-%d-%H')


async def increment_domain_metric(domain: str, counter_name: str):
    """
    Increment a domain metric counter in Redis.

    Falls back to direct DB write if Redis unavailable.

    Args:
        domain: Domain name (e.g., "example.com")
        counter_name: One of: direct_requests, simulated_requests, fallbacks, budget_blocked
    """
    hour_bucket = get_hour_bucket()
    redis_key = f"crawler:metrics:{domain}:{hour_bucket}:{counter_name}"

    try:
        r = await get_redis_client()
        await r.incr(redis_key)
        await r.expire(redis_key, 7 * 24 * 3600)  # 7 days TTL
    except Exception as e:
        logger.warning(f"Redis unavailable, falling back to DB: {e}")
        await _increment_db_atomic(domain, counter_name)


async def get_domain_metrics(domain: str) -> dict:
    """
    Get current hour metrics for a domain.

    Returns:
        dict with keys: direct_requests, simulated_requests, fallbacks, budget_blocked
    """
    hour_bucket = get_hour_bucket()

    try:
        r = await get_redis_client()
        keys = {
            'direct_requests': f"crawler:metrics:{domain}:{hour_bucket}:direct_requests",
            'simulated_requests': f"crawler:metrics:{domain}:{hour_bucket}:simulated_requests",
            'fallbacks': f"crawler:metrics:{domain}:{hour_bucket}:fallbacks",
            'budget_blocked': f"crawler:metrics:{domain}:{hour_bucket}:budget_blocked",
        }

        pipe = r.pipeline()
        for key in keys.values():
            pipe.get(key)
        results = await pipe.execute()

        return {
            name: int(value) if value else 0
            for name, value in zip(keys.keys(), results)
        }
    except Exception:
        return await _get_db_metrics(domain)


async def flush_redis_to_db():
    """
    Periodic task: flush Redis counters to DB.

    Uses lock to prevent concurrent flushes.
    Deletes Redis keys after successful flush.
    """
    LOCK_KEY = "crawler:metrics:flush_lock"

    try:
        r = await get_redis_client()

        # Acquire lock
        if not await r.set(LOCK_KEY, "1", ex=60, nx=True):
            logger.debug("Flush already in progress, skipping")
            return

        try:
            metrics_to_flush = {}

            async for key in r.scan_iter(match="crawler:metrics:*"):
                if key == LOCK_KEY:
                    continue

                parts = key.split(':')
                if len(parts) != 5:
                    continue

                _, _, domain, hour_bucket, counter_name = parts
                value = await r.get(key)
                if not value or int(value) == 0:
                    continue

                key_tuple = (domain, hour_bucket)
                if key_tuple not in metrics_to_flush:
                    metrics_to_flush[key_tuple] = {}

                metrics_to_flush[key_tuple][counter_name] = int(value)

            if metrics_to_flush:
                logger.info(f"Flushing {len(metrics_to_flush)} metric buckets to DB")
                keys_to_delete = await _bulk_increment_db(metrics_to_flush)
                if keys_to_delete:
                    await r.delete(*keys_to_delete)
                    logger.info(f"Deleted {len(keys_to_delete)} Redis keys after flush")
        finally:
            await r.delete(LOCK_KEY)

    except Exception as e:
        logger.error(f"Flush failed: {e}", exc_info=True)


@sync_to_async
def _increment_db_atomic(domain: str, counter_name: str):
    """Atomic increment in DB (fallback when Redis unavailable)."""
    from crawler_admin.models import DomainMetrics
    from django.db.models import F

    now = timezone.now()
    hour_bucket = now.replace(minute=0, second=0, microsecond=0)

    COUNTER_MAP = {
        'direct_requests': 'direct_requests',
        'simulated_requests': 'simulated_requests',
        'fallbacks': 'simulation_fallbacks',
        'budget_blocked': 'simulation_budget_blocked',
    }

    db_field = COUNTER_MAP.get(counter_name, counter_name)

    metric, created = DomainMetrics.objects.get_or_create(
        domain=domain,
        hour_bucket=hour_bucket,
        defaults={
            'direct_requests': 0,
            'simulated_requests': 0,
            'simulation_fallbacks': 0,
            'simulation_budget_blocked': 0,
        }
    )

    DomainMetrics.objects.filter(pk=metric.pk).update(**{
        db_field: F(db_field) + 1
    })


@sync_to_async
def _get_db_metrics(domain: str) -> dict:
    """Get metrics from DB (fallback when Redis unavailable)."""
    from crawler_admin.models import DomainMetrics

    now = timezone.now()
    hour_bucket = now.replace(minute=0, second=0, microsecond=0)

    try:
        metric = DomainMetrics.objects.get(domain=domain, hour_bucket=hour_bucket)
        return {
            'direct_requests': metric.direct_requests,
            'simulated_requests': metric.simulated_requests,
            'fallbacks': metric.simulation_fallbacks,
            'budget_blocked': metric.simulation_budget_blocked,
        }
    except DomainMetrics.DoesNotExist:
        return {
            'direct_requests': 0,
            'simulated_requests': 0,
            'fallbacks': 0,
            'budget_blocked': 0,
        }


@sync_to_async
def _bulk_increment_db(metrics_to_flush: dict) -> list:
    """
    Bulk increment DB counters from Redis data.

    Returns:
        List of Redis keys that were successfully flushed (to be deleted)
    """
    from crawler_admin.models import DomainMetrics
    from django.db.models import F

    COUNTER_MAP = {
        'direct_requests': 'direct_requests',
        'simulated_requests': 'simulated_requests',
        'fallbacks': 'simulation_fallbacks',
        'budget_blocked': 'simulation_budget_blocked',
    }

    keys_to_delete = []

    for (domain, hour_bucket_str), counters in metrics_to_flush.items():
        hour_bucket = datetime.strptime(hour_bucket_str, '%Y-%m-%d-%H')
        hour_bucket = timezone.make_aware(hour_bucket)

        metric, created = DomainMetrics.objects.get_or_create(
            domain=domain,
            hour_bucket=hour_bucket,
            defaults={
                'direct_requests': 0,
                'simulated_requests': 0,
                'simulation_fallbacks': 0,
                'simulation_budget_blocked': 0,
            }
        )

        update_fields = {}
        for counter_name, delta in counters.items():
            db_field = COUNTER_MAP.get(counter_name)
            if db_field and delta > 0:
                update_fields[db_field] = F(db_field) + delta
                keys_to_delete.append(f"crawler:metrics:{domain}:{hour_bucket_str}:{counter_name}")

        if update_fields:
            DomainMetrics.objects.filter(pk=metric.pk).update(**update_fields)

    return keys_to_delete
