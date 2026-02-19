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


async def try_consume_simulated_budget(domain: str, base_allowance: int) -> bool:
    """
    Atomically check and consume simulated request budget.

    Budget formula: max_simulated = max(int(direct_requests * 0.30), base_allowance)

    This operation is atomic to prevent multi-worker overshoot.

    Args:
        domain: Domain name
        base_allowance: Minimum allowance (floor for low-volume domains)

    Returns:
        True if budget consumed successfully, False if budget exhausted
    """
    hour_bucket = get_hour_bucket()

    direct_key = f"crawler:metrics:{domain}:{hour_bucket}:direct_requests"
    simulated_key = f"crawler:metrics:{domain}:{hour_bucket}:simulated_requests"
    blocked_key = f"crawler:metrics:{domain}:{hour_bucket}:budget_blocked"

    # Lua script for atomic budget check and consume
    # Returns: 1 if consumed, 0 if blocked
    lua_script = """
    local direct_key = KEYS[1]
    local simulated_key = KEYS[2]
    local blocked_key = KEYS[3]
    local base_allowance = tonumber(ARGV[1])
    local ttl = tonumber(ARGV[2])

    -- Read current values (default to 0 if nil)
    local direct = tonumber(redis.call('GET', direct_key) or 0)
    local simulated = tonumber(redis.call('GET', simulated_key) or 0)

    -- Calculate max allowed simulated requests
    local max_simulated = math.max(math.floor(direct * 0.30), base_allowance)

    -- Check if budget available
    -- Allow up to and including max_simulated (30 out of 100 = 30% is allowed)
    if simulated > max_simulated then
        -- Budget exhausted, increment blocked counter
        redis.call('INCR', blocked_key)
        redis.call('EXPIRE', blocked_key, ttl)
        return 0
    else
        -- Budget available, increment simulated counter
        redis.call('INCR', simulated_key)
        redis.call('EXPIRE', simulated_key, ttl)
        return 1
    end
    """

    try:
        r = await get_redis_client()
        result = await r.eval(
            lua_script,
            3,  # number of keys
            direct_key,
            simulated_key,
            blocked_key,
            base_allowance,
            7 * 24 * 3600,  # 7 days TTL
        )
        return bool(result)
    except Exception as e:
        logger.warning(f"Redis budget check failed, falling back to DB: {e}")
        return await _try_consume_budget_db(domain, base_allowance)


async def flush_redis_to_db():
    """
    Periodic task: flush Redis counters to DB.

    Uses lock to prevent concurrent flushes.
    Uses atomic GETDEL to prevent losing concurrent increments.
    """
    LOCK_KEY = "crawler:metrics:flush_lock"

    # Lua script for atomic get-and-reset
    # Returns the value and resets to 0, allowing concurrent increments to accumulate
    lua_getdel = """
    local key = KEYS[1]
    local value = redis.call('GET', key)
    if value then
        redis.call('SET', key, 0)
        return value
    else
        return nil
    end
    """

    try:
        r = await get_redis_client()

        # Acquire lock
        if not await r.set(LOCK_KEY, "1", ex=60, nx=True):
            logger.debug("Flush already in progress, skipping")
            return

        try:
            metrics_to_flush = {}
            keys_processed = []

            async for key in r.scan_iter(match="crawler:metrics:*"):
                if key == LOCK_KEY:
                    continue

                parts = key.split(':')
                if len(parts) != 5:
                    continue

                _, _, domain, hour_bucket, counter_name = parts

                # Atomically get value and reset to 0
                value = await r.eval(lua_getdel, 1, key)
                if not value or int(value) == 0:
                    continue

                key_tuple = (domain, hour_bucket)
                if key_tuple not in metrics_to_flush:
                    metrics_to_flush[key_tuple] = {}

                metrics_to_flush[key_tuple][counter_name] = int(value)
                keys_processed.append(key)

            if metrics_to_flush:
                logger.info(f"Flushing {len(metrics_to_flush)} metric buckets to DB")
                await _bulk_increment_db(metrics_to_flush)
                logger.info(f"Flushed {len(keys_processed)} Redis keys (reset to 0, keeping for TTL)")
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
def _try_consume_budget_db(domain: str, base_allowance: int) -> bool:
    """
    DB fallback for atomic budget consumption.

    NOTE: This is best-effort only - cannot guarantee atomicity across processes.
    Use Redis for production multi-worker deployments.
    """
    from crawler_admin.models import DomainMetrics
    from django.db.models import F

    now = timezone.now()
    hour_bucket = now.replace(minute=0, second=0, microsecond=0)

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

    # Read current values
    direct = metric.direct_requests
    simulated = metric.simulated_requests
    max_simulated = max(int(direct * 0.30), base_allowance)

    # Allow up to and including max_simulated (30 out of 100 = 30% is allowed)
    if simulated > max_simulated:
        # Budget exhausted
        DomainMetrics.objects.filter(pk=metric.pk).update(
            simulation_budget_blocked=F('simulation_budget_blocked') + 1
        )
        return False
    else:
        # Budget available
        DomainMetrics.objects.filter(pk=metric.pk).update(
            simulated_requests=F('simulated_requests') + 1
        )
        return True


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
def _bulk_increment_db(metrics_to_flush: dict):
    """
    Bulk increment DB counters from Redis data.

    Applies deltas atomically using F() expressions.
    """
    from crawler_admin.models import DomainMetrics
    from django.db.models import F

    COUNTER_MAP = {
        'direct_requests': 'direct_requests',
        'simulated_requests': 'simulated_requests',
        'fallbacks': 'simulation_fallbacks',
        'budget_blocked': 'simulation_budget_blocked',
    }

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

        if update_fields:
            DomainMetrics.objects.filter(pk=metric.pk).update(**update_fields)
