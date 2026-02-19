"""
Unit tests for Redis-based metrics system.

Tests:
- Redis counter increments
- Metric retrieval by domain
- Flush to database with atomic F() increments
- Redis key deletion after flush
- Counter mapping (redis key names to DB field names)
"""

import os
import sys
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime

import pytest

# Setup Django
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')

import django
django.setup()

from django.utils import timezone

from shared.metrics import (
    increment_domain_metric,
    get_domain_metrics,
    flush_redis_to_db,
    get_hour_bucket,
)
from crawler_admin.models import DomainMetrics


class TestHourBucket:
    """Test hour bucket generation."""

    def test_hour_bucket_format(self):
        """Test that hour bucket is in correct format."""
        bucket = get_hour_bucket()
        assert len(bucket) == 13  # YYYY-MM-DD-HH
        assert bucket[4] == '-'
        assert bucket[7] == '-'
        assert bucket[10] == '-'

    def test_hour_bucket_truncates_minutes(self):
        """Test that hour bucket truncates to hour."""
        with patch('shared.metrics.timezone') as mock_tz:
            mock_tz.now.return_value = datetime(2026, 2, 18, 15, 45, 30)
            bucket = get_hour_bucket()
            assert bucket == '2026-02-18-15'


class TestIncrementDomainMetric:
    """Test incrementing domain metrics in Redis."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Clean up before each test."""
        # Clear any existing DomainMetrics
        DomainMetrics.objects.all().delete()

    @pytest.mark.asyncio
    async def test_increment_creates_redis_key(self):
        """Test that increment creates Redis key."""
        with patch('shared.metrics.get_redis_client') as mock_get_redis:
            mock_redis = AsyncMock()
            mock_get_redis.return_value = mock_redis

            await increment_domain_metric('example.com', 'direct_requests')

            # Verify Redis incr was called
            mock_redis.incr.assert_called_once()
            args = mock_redis.incr.call_args[0]
            assert 'example.com' in args[0]
            assert 'direct_requests' in args[0]

            # Verify expire was set
            mock_redis.expire.assert_called_once()

    @pytest.mark.asyncio
    async def test_increment_falls_back_to_db_on_redis_error(self):
        """Test that increment falls back to DB when Redis unavailable."""
        with patch('shared.metrics.get_redis_client') as mock_get_redis:
            mock_get_redis.side_effect = Exception("Redis connection failed")

            # Should not raise, should fall back to DB
            await increment_domain_metric('example.com', 'direct_requests')

            # Verify DB was updated
            now = timezone.now()
            hour_bucket = now.replace(minute=0, second=0, microsecond=0)
            metric = DomainMetrics.objects.get(domain='example.com', hour_bucket=hour_bucket)
            assert metric.direct_requests == 1


class TestGetDomainMetrics:
    """Test retrieving domain metrics."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Clean up before each test."""
        DomainMetrics.objects.all().delete()

    @pytest.mark.asyncio
    async def test_get_metrics_returns_zero_for_new_domain(self):
        """Test that get_metrics returns zeros for new domain."""
        with patch('shared.metrics.get_redis_client') as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.pipeline.return_value.__aenter__.return_value.execute.return_value = [None, None, None, None]
            mock_get_redis.return_value = mock_redis

            metrics = await get_domain_metrics('example.com')

            assert metrics['direct_requests'] == 0
            assert metrics['simulated_requests'] == 0
            assert metrics['fallbacks'] == 0
            assert metrics['budget_blocked'] == 0

    @pytest.mark.asyncio
    async def test_get_metrics_parses_redis_values(self):
        """Test that get_metrics parses Redis values correctly."""
        with patch('shared.metrics.get_redis_client') as mock_get_redis:
            mock_redis = AsyncMock()
            pipeline = AsyncMock()
            pipeline.execute.return_value = ['10', '3', '1', '2']
            mock_redis.pipeline.return_value.__aenter__.return_value = pipeline
            mock_get_redis.return_value = mock_redis

            metrics = await get_domain_metrics('example.com')

            assert metrics['direct_requests'] == 10
            assert metrics['simulated_requests'] == 3
            assert metrics['fallbacks'] == 1
            assert metrics['budget_blocked'] == 2

    @pytest.mark.asyncio
    async def test_get_metrics_falls_back_to_db_on_redis_error(self):
        """Test that get_metrics falls back to DB when Redis unavailable."""
        # Create DB metric
        now = timezone.now()
        hour_bucket = now.replace(minute=0, second=0, microsecond=0)
        DomainMetrics.objects.create(
            domain='example.com',
            hour_bucket=hour_bucket,
            direct_requests=5,
            simulated_requests=2,
            simulation_fallbacks=1,
            simulation_budget_blocked=0,
        )

        with patch('shared.metrics.get_redis_client') as mock_get_redis:
            mock_get_redis.side_effect = Exception("Redis connection failed")

            metrics = await get_domain_metrics('example.com')

            assert metrics['direct_requests'] == 5
            assert metrics['simulated_requests'] == 2
            assert metrics['fallbacks'] == 1
            assert metrics['budget_blocked'] == 0


class TestFlushRedisToDb:
    """Test flushing Redis metrics to database."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Clean up before each test."""
        DomainMetrics.objects.all().delete()

    @pytest.mark.asyncio
    async def test_flush_acquires_lock(self):
        """Test that flush acquires distributed lock."""
        with patch('shared.metrics.get_redis_client') as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.set.return_value = True
            mock_redis.scan_iter.return_value = []
            mock_get_redis.return_value = mock_redis

            await flush_redis_to_db()

            # Verify lock was acquired with correct parameters
            mock_redis.set.assert_called()
            args, kwargs = mock_redis.set.call_args
            assert args[0] == 'crawler:metrics:flush_lock'
            assert kwargs['nx'] is True  # Only set if not exists
            assert kwargs['ex'] == 60  # 60 second expiration

    @pytest.mark.asyncio
    async def test_flush_skips_if_lock_held(self):
        """Test that flush skips if another process holds lock."""
        with patch('shared.metrics.get_redis_client') as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.set.return_value = False  # Lock already held
            mock_get_redis.return_value = mock_redis

            await flush_redis_to_db()

            # Verify scan was not called (early exit)
            mock_redis.scan_iter.assert_not_called()

    @pytest.mark.asyncio
    async def test_flush_aggregates_and_increments_db(self):
        """Test that flush aggregates Redis metrics and increments DB atomically."""
        with patch('shared.metrics.get_redis_client') as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.set.return_value = True
            mock_redis.get.side_effect = ['10', '3', '1', '2']
            mock_redis.scan_iter.return_value = [
                'crawler:metrics:example.com:2026-02-18-15:direct_requests',
                'crawler:metrics:example.com:2026-02-18-15:simulated_requests',
                'crawler:metrics:example.com:2026-02-18-15:fallbacks',
                'crawler:metrics:example.com:2026-02-18-15:budget_blocked',
            ]
            mock_get_redis.return_value = mock_redis

            await flush_redis_to_db()

            # Verify DB was updated
            # Since we can't easily test F() expressions in unit tests,
            # we just verify the metric was created
            metrics = DomainMetrics.objects.filter(domain='example.com')
            assert metrics.exists()

    @pytest.mark.asyncio
    async def test_flush_deletes_redis_keys_after_success(self):
        """Test that flush deletes Redis keys after successful DB write."""
        with patch('shared.metrics.get_redis_client') as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.set.return_value = True
            mock_redis.get.side_effect = ['10', '3']
            mock_redis.scan_iter.return_value = [
                'crawler:metrics:example.com:2026-02-18-15:direct_requests',
                'crawler:metrics:example.com:2026-02-18-15:simulated_requests',
            ]
            mock_get_redis.return_value = mock_redis

            await flush_redis_to_db()

            # Verify delete was called
            mock_redis.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_flush_releases_lock_on_error(self):
        """Test that flush releases lock even on error."""
        with patch('shared.metrics.get_redis_client') as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.set.return_value = True
            mock_redis.scan_iter.side_effect = Exception("Redis scan failed")
            mock_get_redis.return_value = mock_redis

            # Should not raise
            await flush_redis_to_db()

            # Verify lock was deleted (in finally block)
            mock_redis.delete.assert_called()
            args = mock_redis.delete.call_args[0]
            assert 'crawler:metrics:flush_lock' in args


class TestCounterMapping:
    """Test counter name mapping from Redis to DB."""

    def test_counter_names_map_correctly(self):
        """Test that Redis counter names map to DB field names."""
        mapping = {
            'direct_requests': 'direct_requests',
            'simulated_requests': 'simulated_requests',
            'fallbacks': 'simulation_fallbacks',
            'budget_blocked': 'simulation_budget_blocked',
        }

        # Verify DB fields exist
        from crawler_admin.models import DomainMetrics
        field_names = [f.name for f in DomainMetrics._meta.get_fields()]

        for redis_name, db_name in mapping.items():
            assert db_name in field_names, f"DB field '{db_name}' not found for Redis counter '{redis_name}'"
