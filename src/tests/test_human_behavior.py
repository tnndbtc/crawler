"""
Unit tests for Human Browsing Simulation.

Tests:
- SimulationBudget: 30% cap enforcement
- HumanBrowsingSession: homepage visit, delays, prefetch
- AdaptiveBackoff: penalty application on 429/403
- Request role tracking in RateLimitedClient
"""

import os
import sys
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime, timedelta

import pytest

# Setup Django
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')

import django
django.setup()

from django.utils import timezone

from shared.human_behavior import (
    SimulationBudget,
    HumanBrowsingSession,
    AdaptiveBackoff,
)
from shared.http_client import RateLimitedClient
from crawler_admin.models import DomainPolicy, DomainMetrics, SystemSettings


class TestSimulationBudget:
    """Test budget enforcement for simulated requests."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Clean up before each test."""
        DomainPolicy.objects.all().delete()
        DomainMetrics.objects.all().delete()
        SystemSettings.objects.filter(key__startswith='crawler_human_').delete()

    @pytest.mark.asyncio
    async def test_budget_allows_30_percent(self):
        """Test that budget allows up to 30% simulated requests."""
        budget = SimulationBudget('example.com')

        with patch('shared.metrics.get_domain_metrics') as mock_get_metrics:
            mock_get_metrics.return_value = {
                'direct_requests': 100,
                'simulated_requests': 30,
                'fallbacks': 0,
                'budget_blocked': 0,
            }

            # At exactly 30%, should still be allowed
            result = await budget.check()
            assert result is True

    @pytest.mark.asyncio
    async def test_budget_blocks_over_30_percent(self):
        """Test that budget blocks when over 30%."""
        budget = SimulationBudget('example.com')

        with patch('shared.metrics.get_domain_metrics') as mock_get_metrics:
            mock_get_metrics.return_value = {
                'direct_requests': 100,
                'simulated_requests': 31,  # Over 30%
                'fallbacks': 0,
                'budget_blocked': 0,
            }

            result = await budget.check()
            assert result is False

    @pytest.mark.asyncio
    async def test_budget_respects_base_allowance(self):
        """Test that budget respects base allowance even with zero direct requests."""
        SystemSettings.objects.create(
            key='crawler_human_base_allowance',
            value_json=5,
            value_type='integer',
        )

        budget = SimulationBudget('example.com')

        with patch('shared.metrics.get_domain_metrics') as mock_get_metrics:
            mock_get_metrics.return_value = {
                'direct_requests': 0,  # No direct requests yet
                'simulated_requests': 4,  # Below base allowance
                'fallbacks': 0,
                'budget_blocked': 0,
            }

            result = await budget.check()
            assert result is True

    @pytest.mark.asyncio
    async def test_budget_blocks_over_base_allowance_with_zero_direct(self):
        """Test that budget blocks when over base allowance with zero direct requests."""
        SystemSettings.objects.create(
            key='crawler_human_base_allowance',
            value_json=5,
            value_type='integer',
        )

        budget = SimulationBudget('example.com')

        with patch('shared.metrics.get_domain_metrics') as mock_get_metrics:
            mock_get_metrics.return_value = {
                'direct_requests': 0,
                'simulated_requests': 6,  # Over base allowance
                'fallbacks': 0,
                'budget_blocked': 0,
            }

            result = await budget.check()
            assert result is False

    @pytest.mark.asyncio
    async def test_budget_uses_domain_specific_base_allowance(self):
        """Test that budget uses domain-specific base allowance from DomainPolicy."""
        DomainPolicy.objects.create(
            domain='example.com',
            simulation_config={'base_allowance': 10},
        )

        budget = SimulationBudget('example.com')

        with patch('shared.metrics.get_domain_metrics') as mock_get_metrics:
            mock_get_metrics.return_value = {
                'direct_requests': 0,
                'simulated_requests': 9,
                'fallbacks': 0,
                'budget_blocked': 0,
            }

            result = await budget.check()
            assert result is True


class TestHumanBrowsingSession:
    """Test human browsing session behaviors."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Clean up before each test."""
        DomainPolicy.objects.all().delete()
        SystemSettings.objects.filter(key__startswith='crawler_human_').delete()

    @pytest.mark.asyncio
    async def test_session_loads_config_from_system_settings(self):
        """Test that session loads config from SystemSettings."""
        SystemSettings.objects.create(
            key='crawler_human_mode_enabled',
            value_json=True,
            value_type='boolean',
        )
        SystemSettings.objects.create(
            key='crawler_human_homepage_probability',
            value_json=0.50,
            value_type='float',
        )

        mock_client = AsyncMock(spec=RateLimitedClient)
        session = await HumanBrowsingSession.from_config(mock_client, 'example.com')

        assert session.config['enabled'] is True
        assert session.config['homepage_probability'] == 0.50

    @pytest.mark.asyncio
    async def test_session_disabled_when_mode_disabled(self):
        """Test that session is disabled when mode is disabled in settings."""
        SystemSettings.objects.create(
            key='crawler_human_mode_enabled',
            value_json=False,
            value_type='boolean',
        )

        mock_client = AsyncMock(spec=RateLimitedClient)
        session = await HumanBrowsingSession.from_config(mock_client, 'example.com')

        assert session.config['enabled'] is False

    @pytest.mark.asyncio
    async def test_maybe_visit_homepage_respects_probability(self):
        """Test that homepage visit respects probability."""
        SystemSettings.objects.create(
            key='crawler_human_mode_enabled',
            value_json=True,
            value_type='boolean',
        )
        SystemSettings.objects.create(
            key='crawler_human_homepage_probability',
            value_json=1.0,  # Always visit
            value_type='float',
        )

        mock_client = AsyncMock(spec=RateLimitedClient)
        session = await HumanBrowsingSession.from_config(mock_client, 'example.com')

        with patch('shared.metrics.get_domain_metrics') as mock_metrics:
            mock_metrics.return_value = {
                'direct_requests': 100,
                'simulated_requests': 0,
                'fallbacks': 0,
                'budget_blocked': 0,
            }

            await session.maybe_visit_homepage('https://example.com/feed.xml')

            # Verify HEAD request was made
            mock_client.head.assert_called_once()
            args = mock_client.head.call_args
            assert args[0][0] == 'https://example.com/'
            assert args[1]['request_role'] == 'simulated'

    @pytest.mark.asyncio
    async def test_optional_prefetch_respects_probability(self):
        """Test that prefetch respects probability."""
        SystemSettings.objects.create(
            key='crawler_human_mode_enabled',
            value_json=True,
            value_type='boolean',
        )
        SystemSettings.objects.create(
            key='crawler_human_prefetch_probability',
            value_json=1.0,  # Always prefetch
            value_type='float',
        )

        mock_client = AsyncMock(spec=RateLimitedClient)
        session = await HumanBrowsingSession.from_config(mock_client, 'example.com')

        with patch('shared.metrics.get_domain_metrics') as mock_metrics:
            mock_metrics.return_value = {
                'direct_requests': 100,
                'simulated_requests': 0,
                'fallbacks': 0,
                'budget_blocked': 0,
            }

            await session.optional_prefetch('https://example.com/article.html')

            # Verify HEAD request was made
            mock_client.head.assert_called_once()
            args = mock_client.head.call_args
            assert args[0][0] == 'https://example.com/favicon.ico'
            assert args[1]['request_role'] == 'simulated'

    @pytest.mark.asyncio
    async def test_pre_article_delay_waits(self):
        """Test that pre_article_delay actually delays."""
        SystemSettings.objects.create(
            key='crawler_human_mode_enabled',
            value_json=True,
            value_type='boolean',
        )

        mock_client = AsyncMock(spec=RateLimitedClient)
        session = await HumanBrowsingSession.from_config(mock_client, 'example.com')

        with patch('shared.jitter.jittered_sleep') as mock_sleep:
            await session.pre_article_delay()

            # Verify sleep was called
            mock_sleep.assert_called_once()
            # Check that delay is in expected range (2-15 seconds)
            args = mock_sleep.call_args[0]
            assert 2 <= args[0] <= 15

    @pytest.mark.asyncio
    async def test_session_skips_when_budget_exhausted(self):
        """Test that session skips behaviors when budget exhausted."""
        SystemSettings.objects.create(
            key='crawler_human_mode_enabled',
            value_json=True,
            value_type='boolean',
        )
        SystemSettings.objects.create(
            key='crawler_human_homepage_probability',
            value_json=1.0,
            value_type='float',
        )

        mock_client = AsyncMock(spec=RateLimitedClient)
        session = await HumanBrowsingSession.from_config(mock_client, 'example.com')

        with patch('shared.metrics.get_domain_metrics') as mock_metrics:
            # Budget exhausted
            mock_metrics.return_value = {
                'direct_requests': 100,
                'simulated_requests': 50,  # Way over 30%
                'fallbacks': 0,
                'budget_blocked': 0,
            }

            await session.maybe_visit_homepage('https://example.com/feed.xml')

            # Verify no HEAD request was made
            mock_client.head.assert_not_called()


class TestAdaptiveBackoff:
    """Test adaptive backoff mechanism."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Clean up before each test."""
        DomainPolicy.objects.all().delete()
        SystemSettings.objects.filter(key__startswith='crawler_human_').delete()

    @pytest.mark.asyncio
    async def test_backoff_triggers_on_429(self):
        """Test that backoff is triggered on 429 status code."""
        SystemSettings.objects.create(
            key='crawler_human_backoff_duration_minutes',
            value_json=15,
            value_type='integer',
        )

        backoff = AdaptiveBackoff('example.com')
        await backoff.record_error(429)

        # Verify policy was created with penalty
        policy = DomainPolicy.objects.get(domain='example.com')
        assert policy.simulation_penalty_until is not None
        assert policy.simulation_delay_multiplier == 2.5
        assert policy.simulation_probability_multiplier == 0.5

    @pytest.mark.asyncio
    async def test_backoff_triggers_on_403(self):
        """Test that backoff is triggered on 403 status code."""
        backoff = AdaptiveBackoff('example.com')
        await backoff.record_error(403)

        # Verify policy was created with penalty
        policy = DomainPolicy.objects.get(domain='example.com')
        assert policy.simulation_penalty_until is not None

    @pytest.mark.asyncio
    async def test_backoff_ignores_other_status_codes(self):
        """Test that backoff ignores other status codes."""
        backoff = AdaptiveBackoff('example.com')
        await backoff.record_error(404)

        # Verify no policy was created
        assert not DomainPolicy.objects.filter(domain='example.com').exists()

    @pytest.mark.asyncio
    async def test_backoff_penalty_duration(self):
        """Test that backoff penalty has correct duration."""
        SystemSettings.objects.create(
            key='crawler_human_backoff_duration_minutes',
            value_json=20,
            value_type='integer',
        )

        backoff = AdaptiveBackoff('example.com')
        before = timezone.now()
        await backoff.record_error(429)
        after = timezone.now()

        policy = DomainPolicy.objects.get(domain='example.com')

        # Penalty should expire in ~20 minutes (allowing for test execution time)
        expected_expiry = before + timedelta(minutes=20)
        assert policy.simulation_penalty_until >= expected_expiry
        assert policy.simulation_penalty_until <= after + timedelta(minutes=20, seconds=5)

    @pytest.mark.asyncio
    async def test_backoff_updates_existing_policy(self):
        """Test that backoff updates existing policy instead of creating new one."""
        # Create initial policy
        DomainPolicy.objects.create(domain='example.com')

        backoff = AdaptiveBackoff('example.com')
        await backoff.record_error(429)

        # Verify only one policy exists
        assert DomainPolicy.objects.filter(domain='example.com').count() == 1

        policy = DomainPolicy.objects.get(domain='example.com')
        assert policy.simulation_penalty_until is not None


class TestRequestRoleTracking:
    """Test request role tracking in RateLimitedClient."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Clean up before each test."""
        DomainMetrics.objects.all().delete()

    @pytest.mark.asyncio
    async def test_get_tracks_direct_by_default(self):
        """Test that GET request tracks as direct by default."""
        with patch('shared.metrics.increment_domain_metric') as mock_increment:
            client = RateLimitedClient()

            with patch.object(client, '_check_circuit_breaker', new=AsyncMock()):
                with patch.object(client, '_wait_for_rate_limit', new=AsyncMock()):
                    with patch.object(client, '_acquire_concurrency_slot'):
                        with patch.object(client, '_execute_with_retry', new=AsyncMock(return_value=(Mock(status_code=200), 0))):
                            try:
                                await client.get('https://example.com/page')
                            except:
                                pass  # Ignore errors, we're testing metrics

            # Verify direct_requests was incremented
            mock_increment.assert_called()
            args = mock_increment.call_args[0]
            assert args[1] == 'direct_requests'

    @pytest.mark.asyncio
    async def test_head_tracks_direct_by_default(self):
        """Test that HEAD request tracks as direct by default."""
        with patch('shared.metrics.increment_domain_metric') as mock_increment:
            client = RateLimitedClient()

            with patch.object(client, '_check_circuit_breaker', new=AsyncMock()):
                with patch.object(client, '_wait_for_rate_limit', new=AsyncMock()):
                    with patch.object(client, '_acquire_concurrency_slot'):
                        with patch.object(client, '_execute_head_with_retry', new=AsyncMock(return_value=(Mock(status_code=200), 0))):
                            try:
                                await client.head('https://example.com/')
                            except:
                                pass  # Ignore errors, we're testing metrics

            # Verify direct_requests was incremented
            mock_increment.assert_called()
            args = mock_increment.call_args[0]
            assert args[1] == 'direct_requests'

    @pytest.mark.asyncio
    async def test_head_tracks_simulated_when_specified(self):
        """Test that HEAD request tracks as simulated when explicitly specified."""
        with patch('shared.metrics.increment_domain_metric') as mock_increment:
            client = RateLimitedClient()

            with patch.object(client, '_check_circuit_breaker', new=AsyncMock()):
                with patch.object(client, '_wait_for_rate_limit', new=AsyncMock()):
                    with patch.object(client, '_acquire_concurrency_slot'):
                        with patch.object(client, '_execute_head_with_retry', new=AsyncMock(return_value=(Mock(status_code=200), 0))):
                            try:
                                await client.head('https://example.com/', request_role="simulated")
                            except:
                                pass  # Ignore errors, we're testing metrics

            # Verify simulated_requests was incremented
            mock_increment.assert_called()
            args = mock_increment.call_args[0]
            assert args[1] == 'simulated_requests'

    @pytest.mark.asyncio
    async def test_adaptive_backoff_triggered_on_429(self):
        """Test that adaptive backoff is triggered on 429 response."""
        with patch('shared.human_behavior.AdaptiveBackoff.record_error') as mock_record:
            client = RateLimitedClient()

            with patch.object(client, '_check_circuit_breaker', new=AsyncMock()):
                with patch.object(client, '_wait_for_rate_limit', new=AsyncMock()):
                    with patch.object(client, '_acquire_concurrency_slot'):
                        with patch.object(client, '_execute_with_retry', new=AsyncMock(return_value=(Mock(status_code=429), 0))):
                            with patch('shared.metrics.increment_domain_metric', new=AsyncMock()):
                                with patch.object(client, '_record_metrics', new=AsyncMock()):
                                    try:
                                        await client.get('https://example.com/page')
                                    except:
                                        pass

            # Verify record_error was called with 429
            mock_record.assert_called_once()
