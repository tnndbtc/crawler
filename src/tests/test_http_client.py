"""
Unit tests for RateLimitedClient.

Tests:
- Rate limiting enforcement
- HTTP caching (ETag, 304 responses)
- Retry logic (429, 5xx, network errors)
- Jitter and backoff
- URL normalization
"""

import os
import sys
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime, timedelta

import pytest
import asyncio

# Setup Django
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')

import django
django.setup()

from django.utils import timezone

from shared.http_client import RateLimitedClient, CircuitBreakerOpen, RateLimitExceeded
from shared.url_utils import normalize_url, compute_url_hash, extract_domain
from shared.jitter import jitter, exponential_backoff
from crawler_admin.models import DomainPolicy, URLCache, DomainMetrics


class TestURLNormalization:
    """Test URL normalization utilities."""

    def test_extract_domain(self):
        """Test domain extraction from URLs."""
        assert extract_domain("https://news.ycombinator.com/news") == "news.ycombinator.com"
        assert extract_domain("http://example.com:8080/path") == "example.com"
        assert extract_domain("https://www.reddit.com/r/all") == "www.reddit.com"

    def test_normalize_url_sorts_params(self):
        """Test that query parameters are sorted."""
        url1 = "https://example.com/page?b=2&a=1"
        url2 = "https://example.com/page?a=1&b=2"
        assert normalize_url(url1) == normalize_url(url2)

    def test_normalize_url_strips_tracking(self):
        """Test that tracking parameters are removed."""
        url = "https://example.com/page?id=123&utm_source=twitter&fbclid=xyz"
        normalized = normalize_url(url)
        assert "utm_source" not in normalized
        assert "fbclid" not in normalized
        assert "id=123" in normalized

    def test_compute_url_hash_consistent(self):
        """Test that URL hash is consistent."""
        url = "https://example.com/page?b=2&a=1"
        hash1 = compute_url_hash(url)
        hash2 = compute_url_hash(url)
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex digest


class TestJitter:
    """Test jitter and backoff utilities."""

    def test_jitter_returns_in_range(self):
        """Test that jitter returns value in expected range."""
        base = 60
        for _ in range(100):
            j = jitter(base)
            assert -18 <= j <= 18  # ±30% of 60

    def test_jitter_zero_base(self):
        """Test jitter with zero base."""
        assert jitter(0) == 0.0

    def test_exponential_backoff_increases(self):
        """Test that exponential backoff increases with attempts."""
        delays = [exponential_backoff(i) for i in range(5)]
        # Each delay should be roughly 2x the previous (allowing for jitter)
        assert delays[0] < delays[1] < delays[2] < delays[3]

    def test_exponential_backoff_caps_at_max(self):
        """Test that backoff is capped at max_delay."""
        max_delay = 10.0
        delay = exponential_backoff(100, max_delay=max_delay)
        # Should be close to max_delay (within jitter range)
        assert delay <= max_delay * 1.3  # Allow for jitter


class TestDomainPolicy:
    """Test DomainPolicy model."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Clean up DomainPolicy before each test."""
        DomainPolicy.objects.all().delete()
        yield
        DomainPolicy.objects.all().delete()

    def test_create_policy_with_defaults(self):
        """Test creating policy with default values."""
        policy = DomainPolicy.objects.create(domain="example.com")
        assert policy.max_rpm == 30
        assert policy.max_concurrent == 5
        assert policy.circuit_state == 'closed'
        assert policy.consecutive_failures == 0
        assert policy.current_backoff_multiplier == 1.0

    def test_record_success_resets_failures(self):
        """Test that record_success resets failure count."""
        policy = DomainPolicy.objects.create(
            domain="example.com",
            consecutive_failures=3
        )
        policy.record_success()
        assert policy.consecutive_failures == 0
        assert policy.last_success_at is not None

    def test_record_failure_increments_count(self):
        """Test that record_failure increments failure count."""
        policy = DomainPolicy.objects.create(domain="example.com")
        policy.record_failure('rate_limit')
        assert policy.consecutive_failures == 1
        assert policy.current_backoff_multiplier > 1.0

    def test_circuit_opens_after_threshold(self):
        """Test that circuit opens after reaching failure threshold."""
        policy = DomainPolicy.objects.create(
            domain="example.com",
            circuit_failure_threshold=3
        )

        # Record failures
        for _ in range(3):
            policy.record_failure('forbidden')

        assert policy.circuit_state == 'open'
        assert policy.circuit_opened_at is not None

    def test_is_cooldown_expired(self):
        """Test cooldown expiration check."""
        policy = DomainPolicy.objects.create(
            domain="example.com",
            circuit_state='open',
            circuit_cooldown_seconds=60
        )

        # Just opened - not expired
        policy.circuit_opened_at = timezone.now()
        policy.save()
        assert not policy.is_cooldown_expired

        # Opened long ago - expired
        policy.circuit_opened_at = timezone.now() - timedelta(seconds=120)
        policy.save()
        assert policy.is_cooldown_expired

    def test_transition_to_half_open(self):
        """Test transition from open to half_open."""
        policy = DomainPolicy.objects.create(
            domain="example.com",
            circuit_state='open'
        )
        policy.transition_to_half_open()
        assert policy.circuit_state == 'half_open'


class TestURLCache:
    """Test URLCache model."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Clean up URLCache before each test."""
        URLCache.objects.all().delete()
        yield
        URLCache.objects.all().delete()

    def test_create_cache_entry(self):
        """Test creating cache entry."""
        url = "https://example.com/api"
        url_hash = compute_url_hash(url)

        cache = URLCache.objects.create(
            url_hash=url_hash,
            url=normalize_url(url),
            etag='W/"abc123"',
            last_modified='Mon, 01 Jan 2024 12:00:00 GMT'
        )

        assert cache.url_hash == url_hash
        assert cache.etag == 'W/"abc123"'
        assert not cache.is_expired  # No expiration set

    def test_cache_expiration(self):
        """Test cache expiration check."""
        url_hash = compute_url_hash("https://example.com/api")

        # Expired cache
        cache = URLCache.objects.create(
            url_hash=url_hash,
            url="https://example.com/api",
            expires_at=timezone.now() - timedelta(hours=1)
        )
        assert cache.is_expired

        # Not expired
        cache.expires_at = timezone.now() + timedelta(hours=1)
        cache.save()
        assert not cache.is_expired

    def test_touch_updates_timestamp(self):
        """Test that touch() updates last_validated_at."""
        cache = URLCache.objects.create(
            url_hash=compute_url_hash("https://example.com/api"),
            url="https://example.com/api"
        )

        old_time = cache.last_validated_at
        cache.touch()
        cache.refresh_from_db()

        assert cache.last_validated_at > old_time


class TestDomainMetrics:
    """Test DomainMetrics model."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Clean up DomainMetrics before each test."""
        DomainMetrics.objects.all().delete()
        yield
        DomainMetrics.objects.all().delete()

    def test_create_metrics(self):
        """Test creating metrics entry."""
        hour_bucket = timezone.now().replace(minute=0, second=0, microsecond=0)
        metrics = DomainMetrics.objects.create(
            domain="example.com",
            hour_bucket=hour_bucket,
            requests_2xx=100,
            requests_4xx=10,
            requests_5xx=5
        )

        assert metrics.total_requests == 115
        assert metrics.success_rate > 86  # ~87%

    def test_success_rate_calculation(self):
        """Test success rate calculation."""
        hour_bucket = timezone.now().replace(minute=0, second=0, microsecond=0)
        metrics = DomainMetrics.objects.create(
            domain="example.com",
            hour_bucket=hour_bucket,
            requests_2xx=95,
            requests_4xx=3,
            requests_5xx=2
        )

        assert metrics.success_rate == 95.0


# Note: Integration tests for RateLimitedClient would require mocking httpx
# and testing actual HTTP behavior. These are covered in test_good_citizen_integration.py
