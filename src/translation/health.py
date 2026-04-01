"""
Provider Health Manager - Manages translation provider health status.

Handles:
- Tracking provider health states (available, unavailable_*)
- Detecting system STOPPED state (all providers unavailable)
- Health probes for automatic recovery
- Admin reset capabilities
"""

import logging
from typing import List, Optional, Tuple

from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)


class ProviderHealthManager:
    """
    Manages translation provider health status.

    This is a utility class with static/class methods for managing
    provider health. All methods are async-safe for use in the
    translation worker.

    Usage:
        # Check if system is stopped
        if await ProviderHealthManager.is_stopped():
            logger.warning("System STOPPED: all providers unavailable")

        # Get available providers
        providers = await ProviderHealthManager.get_available_providers()

        # Handle errors
        await ProviderHealthManager.handle_error('claude', error, error_code='quota_exceeded')

        # Mark success
        await ProviderHealthManager.mark_success('claude')
    """

    # Map HTTP status codes to unavailable states
    HTTP_STATUS_TO_STATE = {
        401: 'unavailable_auth',
        403: 'unavailable_auth',
        429: 'unavailable_rate_limit',
        500: 'unavailable_transient',
        502: 'unavailable_transient',
        503: 'unavailable_transient',
        504: 'unavailable_transient',
    }

    # Error codes that indicate billing/quota issues
    QUOTA_ERROR_CODES = {
        'insufficient_quota',
        'billing_hard_limit_reached',
        'billing_not_active',
        'quota_exceeded',
        'rate_limit_exceeded',  # For persistent rate limits
    }

    @classmethod
    async def handle_error(
        cls,
        provider: str,
        error: Exception,
        http_status: Optional[int] = None,
        error_code: Optional[str] = None
    ) -> str:
        """
        Handle a provider error and update health status.

        Args:
            provider: Provider name (e.g., 'claude')
            error: The exception that occurred
            http_status: HTTP status code if available
            error_code: Specific error code (e.g., 'insufficient_quota')

        Returns:
            The new state of the provider
        """
        from .models import ProviderHealth

        # Determine the appropriate unavailable state
        if error_code and error_code in cls.QUOTA_ERROR_CODES:
            state = 'unavailable_funds'
        elif http_status:
            state = cls.HTTP_STATUS_TO_STATE.get(http_status, 'unavailable_transient')
        else:
            state = 'unavailable_transient'

        error_message = str(error)[:500]  # Truncate long messages

        # Update provider health
        @sync_to_async
        def update_health():
            health = ProviderHealth.get_or_create_provider(provider)
            health.mark_unavailable(
                state=state,
                error_message=error_message,
                error_code=error_code or str(http_status) if http_status else None
            )
            return health.state

        new_state = await update_health()

        logger.warning(
            f"Provider {provider} marked as {new_state}: "
            f"code={error_code}, http={http_status}, error={error_message[:100]}"
        )

        return new_state

    @classmethod
    async def mark_success(cls, provider: str):
        """
        Mark a provider as successful (available).

        Called after a successful translation to ensure
        the provider is marked as available.

        Args:
            provider: Provider name (e.g., 'claude')
        """
        from .models import ProviderHealth

        @sync_to_async
        def update_success():
            health = ProviderHealth.get_or_create_provider(provider)
            # If provider was unavailable, mark it available again
            if not health.is_available:
                logger.info(f"Provider {provider} recovered, marking as available")
                health.mark_available()
            else:
                health.record_success()

        await update_success()

    @classmethod
    async def is_stopped(cls) -> bool:
        """
        Check if the system is in STOPPED state.

        The system is STOPPED when ALL providers are unavailable.
        In this state, the worker should stop processing and wait
        for recovery via health probes or admin reset.

        Returns:
            True if all providers are unavailable
        """
        from .models import ProviderHealth

        @sync_to_async
        def check_stopped():
            providers = ProviderHealth.get_all_providers()
            # System is stopped if ALL providers are unavailable
            return all(not p.is_available for p in providers.values())

        return await check_stopped()

    @classmethod
    async def get_available_providers(cls) -> List[str]:
        """
        Get list of available provider names.

        Returns:
            List of provider names that are currently available
        """
        from .models import ProviderHealth

        @sync_to_async
        def get_available():
            providers = ProviderHealth.get_all_providers()
            return [name for name, health in providers.items() if health.is_available]

        return await get_available()

    @classmethod
    async def get_unavailable_providers(cls) -> List[str]:
        """
        Get list of unavailable provider names.

        Returns:
            List of provider names that are currently unavailable
        """
        from .models import ProviderHealth

        @sync_to_async
        def get_unavailable():
            providers = ProviderHealth.get_all_providers()
            return [name for name, health in providers.items() if not health.is_available]

        return await get_unavailable()

    @classmethod
    async def get_health_status(cls) -> dict:
        """
        Get full health status for all providers.

        Returns:
            Dict with provider states and system status
        """
        from .models import ProviderHealth

        @sync_to_async
        def get_status():
            providers = ProviderHealth.get_all_providers()
            available_count = sum(1 for p in providers.values() if p.is_available)
            total_count = len(providers)

            # Determine overall status
            if available_count == total_count:
                overall_status = 'ok'
            elif available_count > 0:
                overall_status = 'degraded'
            else:
                overall_status = 'stopped'

            provider_details = {}
            for name, health in providers.items():
                provider_details[name] = {
                    'state': health.state,
                    'last_error': health.last_error_message,
                    'last_error_code': health.last_error_code,
                    'last_success_at': health.last_success_at.isoformat() if health.last_success_at else None,
                    'consecutive_failures': health.consecutive_failures,
                    'last_state_change_at': health.last_state_change_at.isoformat() if health.last_state_change_at else None,
                }

            return {
                'status': overall_status,
                'is_stopped': overall_status == 'stopped',
                'available_providers': [name for name, h in providers.items() if h.is_available],
                'providers': provider_details,
            }

        return await get_status()

    @classmethod
    async def reset_provider(cls, provider: str):
        """
        Reset a single provider to available state.

        Used by admin action to manually recover a provider.

        Args:
            provider: Provider name to reset
        """
        from .models import ProviderHealth

        @sync_to_async
        def do_reset():
            health = ProviderHealth.get_or_create_provider(provider)
            health.mark_available()
            logger.info(f"Provider {provider} manually reset to available")

        await do_reset()

    @classmethod
    async def reset_all_providers(cls):
        """
        Reset all providers to available state.

        Used by admin action to clear STOPPED state.
        """
        from .models import ProviderHealth

        @sync_to_async
        def do_reset_all():
            ProviderHealth.reset_all()
            logger.info("All providers manually reset to available")

        await do_reset_all()

    @classmethod
    async def run_health_probe(
        cls,
        provider: str,
        test_func
    ) -> Tuple[bool, Optional[str]]:
        """
        Run a health probe for a specific provider.

        Tests the provider with a lightweight translation and
        marks it as available if successful.

        Args:
            provider: Provider name to test
            test_func: Async function that tests the provider
                      (should raise exception on failure)

        Returns:
            (success: bool, error_message: str or None)
        """
        try:
            await test_func(provider)
            await cls.mark_success(provider)
            logger.info(f"Health probe succeeded for {provider}")
            return (True, None)
        except Exception as e:
            error_msg = str(e)
            logger.debug(f"Health probe failed for {provider}: {error_msg}")
            return (False, error_msg)
