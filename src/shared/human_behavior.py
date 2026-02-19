"""
Human browsing simulation layer for web crawler.

Adds optional human-like behaviors to reduce bot detection:
- Homepage visits before article fetches
- Natural reading delays
- Favicon prefetch requests
- Adaptive backoff on rate limit errors

All simulated requests are budget-enforced (30% cap) and flow through RateLimitedClient.
"""
import random
import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from shared.http_client import RateLimitedClient

from shared.jitter import jittered_sleep
from shared.http_client import CircuitBreakerOpen

logger = logging.getLogger(__name__)


class SimulationBudget:
    """
    Enforces budget for simulated requests.

    Budget formula: max_simulated = max(int(direct_requests * 0.30), base_allowance)

    Base allowance: 1 per domain per hour (default, configurable per domain)
    """

    def __init__(self, domain: str):
        self.domain = domain
        self._base_allowance: Optional[int] = None

    async def check(self) -> bool:
        """
        Check if simulation budget available for this domain.

        Returns:
            True if budget available, False if exhausted
        """
        from shared.metrics import increment_domain_metric, get_domain_metrics

        metrics = await get_domain_metrics(self.domain)
        direct = metrics['direct_requests']
        simulated = metrics['simulated_requests']

        if self._base_allowance is None:
            self._base_allowance = await self._get_base_allowance()

        max_simulated = max(int(direct * 0.30), self._base_allowance)

        if simulated >= max_simulated:
            await increment_domain_metric(self.domain, 'budget_blocked')
            logger.debug(f"Budget exhausted for {self.domain}: {simulated}/{max_simulated}")
            return False

        return True

    async def _get_base_allowance(self) -> int:
        """Get base allowance from domain policy or system settings."""
        from crawler_admin.models import DomainPolicy, SystemSettings
        from asgiref.sync import sync_to_async

        @sync_to_async
        def load():
            try:
                policy = DomainPolicy.objects.get(domain=self.domain)
                if policy.simulation_config and 'base_allowance' in policy.simulation_config:
                    return policy.simulation_config['base_allowance']
            except DomainPolicy.DoesNotExist:
                pass
            return SystemSettings.get_setting('crawler_human_base_allowance', default=1)

        return await load()


class HumanBrowsingSession:
    """
    Manages human-like browsing behaviors for a crawl session.

    Three optional behaviors:
    1. maybe_visit_homepage: HEAD request to / (20% probability)
    2. pre_article_delay: 2-15s log-normal delay (no request)
    3. optional_prefetch: HEAD request to /favicon.ico (15% probability)

    All behaviors respect budget and adaptive backoff penalties.
    """

    def __init__(self, client: 'RateLimitedClient', domain: str):
        self.client = client
        self.domain = domain
        self.config: Optional[dict] = None
        self._budget = SimulationBudget(domain)

    @classmethod
    async def from_config(cls, client: 'RateLimitedClient', domain: str):
        """Create session and load configuration."""
        session = cls(client, domain)
        await session._load_config()
        return session

    async def maybe_visit_homepage(self, feed_url: str):
        """
        Optional HEAD / request to simulate homepage visit.

        Args:
            feed_url: Feed URL to infer homepage from
        """
        if not self.config['enabled']:
            return

        if not await self._budget.check():
            return

        probability = self.config['homepage_probability']
        probability *= self.config.get('probability_multiplier', 1.0)

        if random.random() > probability:
            return

        try:
            homepage_url = self._infer_homepage_url(feed_url)
            logger.debug(f"Visiting homepage: {homepage_url}")
            await self.client.head(homepage_url, request_role="simulated")
            await self._delay_lognormal(1, 2, 5)
        except CircuitBreakerOpen:
            raise
        except Exception as e:
            logger.debug(f"Homepage visit failed for {self.domain}: {e}")
            await self._record_fallback()

    async def pre_article_delay(self):
        """
        Reading delay before article fetch (2-15 seconds, log-normal).

        This simulates natural reading/browsing time. No request made.
        """
        if not self.config['enabled']:
            return

        min_s, mode_s, max_s = 2, 5, 15
        multiplier = self.config.get('delay_multiplier', 1.0)

        await self._delay_lognormal(
            min_s * multiplier,
            mode_s * multiplier,
            max_s * multiplier
        )

    async def optional_prefetch(self, article_url: str):
        """
        Optional HEAD /favicon.ico request to simulate browser prefetch.

        Args:
            article_url: Article URL to infer favicon from
        """
        if not self.config['enabled']:
            return

        if not await self._budget.check():
            return

        probability = self.config['prefetch_probability']
        probability *= self.config.get('probability_multiplier', 1.0)

        if random.random() > probability:
            return

        try:
            favicon_url = self._infer_favicon_url(article_url)
            logger.debug(f"Prefetching favicon: {favicon_url}")
            await self.client.head(favicon_url, request_role="simulated")
        except CircuitBreakerOpen:
            raise
        except Exception as e:
            logger.debug(f"Prefetch failed for {self.domain}: {e}")
            await self._record_fallback()

    async def _load_config(self):
        """Load simulation configuration from DB."""
        from crawler_admin.models import DomainPolicy, SystemSettings
        from asgiref.sync import sync_to_async
        from django.utils import timezone

        @sync_to_async
        def load():
            if not SystemSettings.get_setting('crawler_human_mode_enabled', default=False):
                return {'enabled': False}

            config = {
                'enabled': True,
                'homepage_probability': SystemSettings.get_setting('crawler_human_homepage_probability', default=0.20),
                'prefetch_probability': SystemSettings.get_setting('crawler_human_prefetch_probability', default=0.15),
            }

            try:
                policy = DomainPolicy.objects.get(domain=self.domain)
                if policy.simulation_config:
                    config.update(policy.simulation_config)

                # Apply penalty if active
                if policy.simulation_penalty_until and timezone.now() < policy.simulation_penalty_until:
                    config['probability_multiplier'] = policy.simulation_probability_multiplier
                    config['delay_multiplier'] = policy.simulation_delay_multiplier
            except DomainPolicy.DoesNotExist:
                pass

            return config

        self.config = await load()

    def _infer_homepage_url(self, feed_url: str) -> str:
        """Extract homepage URL from feed URL."""
        from urllib.parse import urlparse
        parsed = urlparse(feed_url)
        return f"{parsed.scheme}://{parsed.netloc}/"

    def _infer_favicon_url(self, article_url: str) -> str:
        """Extract favicon URL from article URL."""
        from urllib.parse import urlparse
        parsed = urlparse(article_url)
        return f"{parsed.scheme}://{parsed.netloc}/favicon.ico"

    async def _delay_lognormal(self, min_s: float, mode_s: float, max_s: float):
        """
        Log-normal delay with fallback to triangular.

        Args:
            min_s: Minimum delay (seconds)
            mode_s: Mode/peak delay (seconds)
            max_s: Maximum delay (seconds)
        """
        try:
            import numpy as np
            mu = np.log(mode_s)
            sigma = 0.5
            delay = np.random.lognormal(mu, sigma)
            delay = max(min_s, min(delay, max_s))
        except ImportError:
            delay = random.triangular(min_s, max_s, mode_s)

        logger.debug(f"Delaying {delay:.1f}s for {self.domain}")
        await jittered_sleep(delay, percent=0.2)

    async def _record_fallback(self):
        """Record simulation fallback (failed attempt)."""
        from shared.metrics import increment_domain_metric
        await increment_domain_metric(self.domain, 'fallbacks')


class AdaptiveBackoff:
    """
    Adaptive backoff mechanism triggered by rate limit errors.

    When domain returns 429 or 403:
    - Sets 15-minute penalty window
    - Reduces simulation probability by 50%
    - Increases delays by 2.5x
    """

    def __init__(self, domain: str):
        self.domain = domain

    async def record_error(self, status_code: int):
        """
        Record rate limit error and apply penalty.

        Args:
            status_code: HTTP status code (429 or 403 trigger backoff)
        """
        if status_code not in (429, 403):
            return

        logger.warning(f"Rate limit error {status_code} for {self.domain}, applying backoff")

        from crawler_admin.models import DomainPolicy, SystemSettings
        from asgiref.sync import sync_to_async
        from django.utils import timezone
        from datetime import timedelta

        @sync_to_async
        def persist():
            duration_minutes = SystemSettings.get_setting('crawler_human_backoff_duration_minutes', default=15)
            penalty_until = timezone.now() + timedelta(minutes=duration_minutes)

            policy, created = DomainPolicy.objects.get_or_create(domain=self.domain)
            policy.simulation_penalty_until = penalty_until
            policy.simulation_delay_multiplier = 2.5
            policy.simulation_probability_multiplier = 0.5
            policy.save(update_fields=[
                'simulation_penalty_until',
                'simulation_delay_multiplier',
                'simulation_probability_multiplier',
            ])

            logger.info(
                f"Applied backoff penalty to {self.domain} until {penalty_until} "
                f"(prob=0.5x, delay=2.5x)"
            )

        await persist()
