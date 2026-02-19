"""
Rate-limited HTTP client with circuit breaker, caching, and metrics.

Drop-in replacement for httpx.AsyncClient that adds:
- Per-domain rate limiting (requests per minute)
- Concurrency limits (max simultaneous requests per domain)
- Circuit breaker (auto-skip failing domains)
- HTTP caching (ETag, Last-Modified, 304 responses)
- Adaptive backoff on 429/403
- Retry logic with exponential backoff
- Metrics collection for observability

Usage:
    from shared.http_client import RateLimitedClient

    async with RateLimitedClient() as client:
        response = await client.get("https://example.com/api")
        # Automatically handles rate limiting, caching, retries, etc.

Feature flags (via SystemSettings):
- enable_rate_limiting: Default False
- enable_circuit_breaker: Default False
- enable_http_caching: Default False

Note: Gracefully degrades when Django is not configured (standalone usage).
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from urllib.parse import urlparse

import httpx

from shared.url_utils import extract_domain, normalize_url, compute_url_hash
from shared.jitter import jitter, exponential_backoff

logger = logging.getLogger(__name__)

# Try to import Django components, but make them optional
try:
    from django.utils import timezone
    from django.core.exceptions import ObjectDoesNotExist
    from asgiref.sync import sync_to_async
    DJANGO_AVAILABLE = True
except ImportError:
    DJANGO_AVAILABLE = False
    # Fallback implementations for non-Django usage
    from datetime import timezone as tz

    class timezone:
        @staticmethod
        def now():
            return datetime.now(tz.utc)

    class ObjectDoesNotExist(Exception):
        pass

    # Simple sync_to_async that just runs synchronously
    def sync_to_async(func):
        async def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper


class CircuitBreakerOpen(Exception):
    """Raised when circuit breaker is open for a domain."""

    def __init__(self, domain: str, cooldown_remaining: float):
        self.domain = domain
        self.cooldown_remaining = cooldown_remaining
        super().__init__(
            f"Circuit breaker open for {domain} "
            f"(cooldown: {cooldown_remaining:.0f}s remaining)"
        )


class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded for a domain."""

    def __init__(self, domain: str, retry_after: float):
        self.domain = domain
        self.retry_after = retry_after
        super().__init__(
            f"Rate limit exceeded for {domain} "
            f"(retry after: {retry_after:.0f}s)"
        )


class RateLimitedClient:
    """
    Rate-limited HTTP client with circuit breaker and caching.

    This is a wrapper around httpx.AsyncClient that adds resilience features
    to make the crawler a "good citizen" and reduce block risk.
    """

    def __init__(
        self,
        *args,
        timeout: float = 30.0,
        follow_redirects: bool = True,
        **kwargs
    ):
        """
        Initialize rate-limited client.

        Args:
            timeout: Default timeout in seconds (default: 30.0)
            follow_redirects: Whether to follow redirects (default: True)
            **kwargs: Additional arguments passed to httpx.AsyncClient
        """
        # Create underlying httpx client
        self._client = httpx.AsyncClient(
            *args,
            timeout=timeout,
            follow_redirects=follow_redirects,
            **kwargs
        )

        # Per-domain concurrency semaphores
        # Key: domain, Value: asyncio.Semaphore
        self._domain_semaphores: Dict[str, asyncio.Semaphore] = {}

        # Per-domain request timestamps for rate limiting
        # Key: domain, Value: list of timestamps
        self._domain_request_times: Dict[str, list] = {}

        # Feature flags cache (loaded from SystemSettings)
        self._feature_flags: Optional[Dict[str, Any]] = None

    async def __aenter__(self):
        """Async context manager entry."""
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *args):
        """Async context manager exit."""
        await self._client.__aexit__(*args)

    async def close(self):
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def _load_feature_flags(self) -> Dict[str, Any]:
        """
        Load feature flags from SystemSettings.

        Returns:
            Dict with feature flag values
        """
        if self._feature_flags is not None:
            return self._feature_flags

        # If Django is not available, return safe defaults
        if not DJANGO_AVAILABLE:
            logger.debug("Django not available, using default feature flags")
            self._feature_flags = {
                'enable_rate_limiting': False,
                'enable_circuit_breaker': False,
                'enable_http_caching': False,
                'default_max_rpm': 30,
                'default_max_concurrent': 5,
            }
            return self._feature_flags

        try:
            from crawler_admin.models import SystemSettings

            # Load flags using sync_to_async to avoid Django async safety issues
            @sync_to_async
            def get_flags():
                return {
                    'enable_rate_limiting': SystemSettings.get_setting('enable_rate_limiting', default=False),
                    'enable_circuit_breaker': SystemSettings.get_setting('enable_circuit_breaker', default=False),
                    'enable_http_caching': SystemSettings.get_setting('enable_http_caching', default=False),
                    'default_max_rpm': SystemSettings.get_setting('default_domain_max_rpm', default=30),
                    'default_max_concurrent': SystemSettings.get_setting('default_domain_max_concurrent', default=5),
                }

            self._feature_flags = await get_flags()
            return self._feature_flags

        except Exception as e:
            logger.debug(f"Failed to load feature flags from SystemSettings: {e}")
            # Safe defaults (all features disabled)
            self._feature_flags = {
                'enable_rate_limiting': False,
                'enable_circuit_breaker': False,
                'enable_http_caching': False,
                'default_max_rpm': 30,
                'default_max_concurrent': 5,
            }
            return self._feature_flags

    @sync_to_async
    def _get_domain_policy(self, domain: str):
        """
        Get or create DomainPolicy for domain.

        Args:
            domain: Domain name

        Returns:
            DomainPolicy instance or None if Django not available
        """
        if not DJANGO_AVAILABLE:
            return None

        try:
            from crawler_admin.models import DomainPolicy
            return DomainPolicy.get_or_create_policy(domain)
        except Exception as e:
            logger.debug(f"Failed to get domain policy: {e}")
            return None

    @sync_to_async
    def _get_url_cache(self, url: str):
        """
        Get URLCache entry for URL.

        Args:
            url: URL to look up

        Returns:
            URLCache instance or None
        """
        if not DJANGO_AVAILABLE:
            return None

        try:
            from crawler_admin.models import URLCache

            url_hash = compute_url_hash(url)
            cache_entry = URLCache.objects.get(url_hash=url_hash)

            # Check if expired
            if cache_entry.is_expired:
                return None

            return cache_entry
        except ObjectDoesNotExist:
            return None
        except Exception as e:
            logger.debug(f"Failed to get URL cache: {e}")
            return None

    @sync_to_async
    def _update_url_cache(self, url: str, response: httpx.Response):
        """
        Update or create URLCache entry from response.

        Args:
            url: URL
            response: httpx.Response object
        """
        if not DJANGO_AVAILABLE:
            return

        try:
            from crawler_admin.models import URLCache

            url_hash = compute_url_hash(url)
            normalized_url = normalize_url(url)

            # Extract caching headers
            etag = response.headers.get('ETag')
            last_modified = response.headers.get('Last-Modified')

            # Parse Cache-Control or Expires header for expiration
            expires_at = None
            cache_control = response.headers.get('Cache-Control', '')
            if 'max-age=' in cache_control:
                # Extract max-age value
                try:
                    max_age_part = [part for part in cache_control.split(',') if 'max-age=' in part][0]
                    max_age = int(max_age_part.split('=')[1].strip())
                    expires_at = timezone.now() + timedelta(seconds=max_age)
                except (ValueError, IndexError):
                    pass
            elif 'Expires' in response.headers:
                # TODO: Parse Expires header (RFC 2616 format)
                pass

            # Update or create cache entry
            URLCache.objects.update_or_create(
                url_hash=url_hash,
                defaults={
                    'url': normalized_url,
                    'etag': etag,
                    'last_modified': last_modified,
                    'expires_at': expires_at,
                }
            )
        except Exception as e:
            logger.debug(f"Failed to update URL cache: {e}")

    @sync_to_async
    def _touch_url_cache(self, url: str):
        """
        Touch URLCache entry (update last_validated_at).

        Args:
            url: URL
        """
        if not DJANGO_AVAILABLE:
            return

        try:
            from crawler_admin.models import URLCache

            url_hash = compute_url_hash(url)
            cache_entry = URLCache.objects.get(url_hash=url_hash)
            cache_entry.touch()
        except ObjectDoesNotExist:
            pass
        except Exception as e:
            logger.debug(f"Failed to touch URL cache: {e}")

    @sync_to_async
    def _record_metrics(
        self,
        domain: str,
        status_code: int,
        latency_ms: int,
        retry_count: int = 0
    ):
        """
        Record metrics for observability.

        Args:
            domain: Domain name
            status_code: HTTP status code
            latency_ms: Request latency in milliseconds
            retry_count: Number of retries performed
        """
        if not DJANGO_AVAILABLE:
            logger.debug("Django not available, skipping metrics recording")
            return

        try:
            from crawler_admin.models import DomainMetrics

            # Get current hour bucket
            now = timezone.now()
            hour_bucket = now.replace(minute=0, second=0, microsecond=0)

            # Update or create metrics entry
            metrics, created = DomainMetrics.objects.get_or_create(
                domain=domain,
                hour_bucket=hour_bucket,
                defaults={
                    'requests_2xx': 0,
                    'requests_4xx': 0,
                    'requests_5xx': 0,
                    'requests_429': 0,
                    'requests_403': 0,
                    'retry_count': 0,
                }
            )

            # Increment appropriate counter
            if 200 <= status_code < 300:
                metrics.requests_2xx += 1
            elif 300 <= status_code < 400:
                # 3xx redirects/not modified - count as 2xx for metrics
                metrics.requests_2xx += 1
            elif 400 <= status_code < 500:
                metrics.requests_4xx += 1
                if status_code == 429:
                    metrics.requests_429 += 1
                elif status_code == 403:
                    metrics.requests_403 += 1
            elif 500 <= status_code < 600:
                metrics.requests_5xx += 1

            # Add retry count
            metrics.retry_count += retry_count

            # Update latency percentiles (simplified: just track p50 as average)
            # In production, you'd use a proper percentile algorithm or library
            if metrics.latency_p50 is None:
                metrics.latency_p50 = latency_ms
            else:
                # Running average (simplified)
                total_requests = metrics.requests_2xx + metrics.requests_4xx + metrics.requests_5xx
                if total_requests > 0:
                    metrics.latency_p50 = int(
                        (metrics.latency_p50 * (total_requests - 1) + latency_ms) / total_requests
                    )

            metrics.save()
        except Exception as e:
            logger.debug(f"Failed to record metrics: {e}")

    async def _check_circuit_breaker(self, domain: str):
        """
        Check circuit breaker state and potentially transition.

        Args:
            domain: Domain name

        Raises:
            CircuitBreakerOpen: If circuit is open and cooldown not expired
        """
        flags = await self._load_feature_flags()
        if not flags['enable_circuit_breaker']:
            return

        policy = await self._get_domain_policy(domain)

        if policy.is_circuit_open:
            if policy.is_cooldown_expired:
                # Transition to half-open
                await sync_to_async(policy.transition_to_half_open)()
            else:
                # Circuit is open, reject request
                cooldown_remaining = (
                    policy.circuit_cooldown_seconds -
                    (timezone.now() - policy.circuit_opened_at).total_seconds()
                )
                raise CircuitBreakerOpen(domain, cooldown_remaining)

    async def _wait_for_rate_limit(self, domain: str):
        """
        Wait if necessary to respect rate limit.

        Args:
            domain: Domain name
        """
        flags = await self._load_feature_flags()
        if not flags['enable_rate_limiting']:
            return

        policy = await self._get_domain_policy(domain)
        max_rpm = policy.max_rpm

        # Initialize request times list for this domain
        if domain not in self._domain_request_times:
            self._domain_request_times[domain] = []

        now = time.time()
        request_times = self._domain_request_times[domain]

        # Remove timestamps older than 1 minute
        cutoff = now - 60
        request_times[:] = [t for t in request_times if t > cutoff]

        # Check if we're at the limit
        if len(request_times) >= max_rpm:
            # Wait until oldest request falls outside the window
            oldest = request_times[0]
            wait_time = 60 - (now - oldest) + jitter(1.0)  # Add jitter
            logger.debug(f"Rate limit hit for {domain}, waiting {wait_time:.1f}s")
            await asyncio.sleep(wait_time)

        # Record this request
        request_times.append(now)

    @asynccontextmanager
    async def _acquire_concurrency_slot(self, domain: str):
        """
        Acquire concurrency slot for domain.

        Args:
            domain: Domain name

        Yields:
            None (context manager)
        """
        flags = await self._load_feature_flags()
        if not flags['enable_rate_limiting']:
            # No concurrency limiting, just yield
            yield
            return

        # Get or create semaphore for this domain
        if domain not in self._domain_semaphores:
            policy = await self._get_domain_policy(domain)
            max_concurrent = policy.max_concurrent
            self._domain_semaphores[domain] = asyncio.Semaphore(max_concurrent)

        # Acquire semaphore
        async with self._domain_semaphores[domain]:
            yield

    async def get(
        self,
        url: str,
        **kwargs
    ) -> httpx.Response:
        """
        Perform GET request with rate limiting, circuit breaker, and caching.

        Args:
            url: URL to fetch
            **kwargs: Additional arguments passed to httpx.AsyncClient.get()

        Returns:
            httpx.Response object

        Raises:
            CircuitBreakerOpen: If circuit breaker is open
            httpx.HTTPStatusError: On HTTP errors (after retries)
            httpx.RequestError: On network errors (after retries)
        """
        domain = extract_domain(url)
        start_time = time.time()

        # Check circuit breaker
        await self._check_circuit_breaker(domain)

        # Wait for rate limit
        await self._wait_for_rate_limit(domain)

        # Acquire concurrency slot
        async with self._acquire_concurrency_slot(domain):
            # Check HTTP cache
            headers = kwargs.get('headers', {}).copy()
            flags = await self._load_feature_flags()

            if flags['enable_http_caching']:
                cache_entry = await self._get_url_cache(url)
                if cache_entry:
                    if cache_entry.etag:
                        headers['If-None-Match'] = cache_entry.etag
                    if cache_entry.last_modified:
                        headers['If-Modified-Since'] = cache_entry.last_modified

            kwargs['headers'] = headers

            # Execute request with retry logic
            response, retry_count = await self._execute_with_retry(url, domain, **kwargs)

            # Record latency
            latency_ms = int((time.time() - start_time) * 1000)

            # Handle 304 Not Modified
            if response.status_code == 304:
                logger.debug(f"HTTP 304 Not Modified: {url}")
                await self._touch_url_cache(url)
                # Mark response as from cache
                response.from_cache = True  # type: ignore

            # Update cache on successful response
            elif flags['enable_http_caching'] and 200 <= response.status_code < 300:
                await self._update_url_cache(url, response)

            # Record metrics
            await self._record_metrics(domain, response.status_code, latency_ms, retry_count)

            # Record success in domain policy
            if flags['enable_circuit_breaker'] and 200 <= response.status_code < 300:
                policy = await self._get_domain_policy(domain)
                await sync_to_async(policy.record_success)()

            # Log structured data
            logger.info(
                "HTTP request completed",
                extra={
                    'domain': domain,
                    'url': url,
                    'status_code': response.status_code,
                    'latency_ms': latency_ms,
                    'retry_count': retry_count,
                    'from_cache': getattr(response, 'from_cache', False),
                }
            )

            return response

    async def _execute_with_retry(
        self,
        url: str,
        domain: str,
        **kwargs
    ) -> tuple[httpx.Response, int]:
        """
        Execute HTTP request with retry logic.

        Args:
            url: URL to fetch
            domain: Domain name
            **kwargs: Arguments for httpx.AsyncClient.get()

        Returns:
            Tuple of (response, retry_count)

        Raises:
            httpx.HTTPStatusError: On unrecoverable HTTP errors
            httpx.RequestError: On network errors after retries
        """
        max_attempts = 3
        flags = await self._load_feature_flags()

        for attempt in range(max_attempts):
            try:
                response = await self._client.get(url, **kwargs)

                # Handle 429 Rate Limit
                if response.status_code == 429:
                    if attempt < max_attempts - 1:
                        # Respect Retry-After header
                        retry_after = int(response.headers.get('Retry-After', 60))
                        wait_time = retry_after + jitter(retry_after)

                        logger.warning(
                            f"HTTP 429 for {domain}, waiting {wait_time:.0f}s "
                            f"(attempt {attempt + 1}/{max_attempts})"
                        )

                        # Record failure and increase backoff
                        if flags['enable_circuit_breaker']:
                            policy = await self._get_domain_policy(domain)
                            await sync_to_async(policy.record_failure)('rate_limit')

                        await asyncio.sleep(wait_time)
                        continue

                # Handle 403 Forbidden (potential block)
                elif response.status_code == 403:
                    logger.error(f"HTTP 403 Forbidden for {domain} - potential block")

                    # Record failure (counts toward circuit breaker)
                    if flags['enable_circuit_breaker']:
                        policy = await self._get_domain_policy(domain)
                        await sync_to_async(policy.record_failure)('forbidden')

                    # Don't retry 403
                    return response, attempt

                # Handle 404 Not Found
                elif response.status_code == 404:
                    # Don't retry 404, return immediately
                    return response, attempt

                # Handle 5xx Server Error
                elif 500 <= response.status_code < 600:
                    if attempt < max_attempts - 1:
                        # Exponential backoff
                        wait_time = exponential_backoff(attempt)
                        logger.warning(
                            f"HTTP {response.status_code} for {domain}, "
                            f"retrying in {wait_time:.1f}s (attempt {attempt + 1}/{max_attempts})"
                        )
                        await asyncio.sleep(wait_time)
                        continue

                # Success or unrecoverable error
                return response, attempt

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                if attempt < max_attempts - 1:
                    # Exponential backoff
                    wait_time = exponential_backoff(attempt)
                    logger.warning(
                        f"Network error for {domain}: {e}, "
                        f"retrying in {wait_time:.1f}s (attempt {attempt + 1}/{max_attempts})"
                    )
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    # Record failure
                    if flags['enable_circuit_breaker']:
                        policy = await self._get_domain_policy(domain)
                        await sync_to_async(policy.record_failure)('generic')
                    raise

        # Should never reach here, but for type safety
        return response, max_attempts - 1
