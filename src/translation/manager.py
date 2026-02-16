"""
Translation Manager - Orchestrates translation with fallback and caching.

The TranslationManager is the main entry point for translation operations.
It handles:
- Engine selection based on configuration
- Fallback handling when engines fail
- In-memory caching for duplicate translations
- Coordination between canonical and display translations
- Provider health tracking and failover

Provider Failover Behavior:
- On quota/auth errors: immediately mark provider unavailable, try next
- When all providers unavailable: return system_stopped error
- Items stay as 'pending' when providers unavailable (not marked 'failed')
"""

import hashlib
import logging
from typing import Dict, Optional, Any, List, TYPE_CHECKING

from asgiref.sync import sync_to_async

from .base import (
    TranslationResult,
    BaseTranslationEngine,
    TranslationError,
    QuotaExceededError,
    AuthenticationError,
    RateLimitError,
)
from .engines import get_engine_class
from .health import ProviderHealthManager

if TYPE_CHECKING:
    from .models import TranslationConfig

logger = logging.getLogger(__name__)


class TranslationManager:
    """
    Orchestrates translation operations with fallback and caching.

    Usage:
        manager = TranslationManager()

        # Load config first (required for async context)
        await manager.load_config()

        # Canonical translation (for ranking/analysis)
        result = await manager.translate_canonical(item)

        # Display translation (for UI)
        result = await manager.translate_display(item, 'zh-Hans')
    """

    def __init__(self):
        """Initialize TranslationManager."""
        self._config: Optional['TranslationConfig'] = None
        self._engines: Dict[str, BaseTranslationEngine] = {}
        self._cache: Dict[str, TranslationResult] = {}

        logger.debug("TranslationManager initialized")

    @property
    def config(self) -> 'TranslationConfig':
        """
        Get cached configuration (sync access).

        Note: Call load_config() first in async context.

        Returns:
            TranslationConfig instance
        """
        if self._config is None:
            raise RuntimeError(
                "Config not loaded. Call 'await manager.load_config()' first "
                "or use 'await manager.get_config()' in async context."
            )
        return self._config

    async def load_config(self) -> 'TranslationConfig':
        """
        Load configuration from database (async-safe).

        Call this before accessing config in async context.

        Returns:
            TranslationConfig instance
        """
        if self._config is None:
            self._config = await self._fetch_config()
        return self._config

    async def get_config(self) -> 'TranslationConfig':
        """
        Get configuration, loading if necessary (async-safe).

        Returns:
            TranslationConfig instance
        """
        return await self.load_config()

    @sync_to_async
    def _fetch_config(self) -> 'TranslationConfig':
        """Fetch config from database (wrapped for async)."""
        from .models import TranslationConfig
        return TranslationConfig.get_config()

    def refresh_config(self):
        """Mark config for reload on next access."""
        self._config = None

    async def reload_config(self) -> 'TranslationConfig':
        """Force reload of configuration from database."""
        self._config = None
        return await self.load_config()

    def _get_engine(self, engine_name: str) -> BaseTranslationEngine:
        """
        Get or create engine instance.

        Args:
            engine_name: Engine name (e.g., 'deepl', 'openai', 'local')

        Returns:
            Engine instance
        """
        if engine_name not in self._engines:
            try:
                engine_class = get_engine_class(engine_name)
                self._engines[engine_name] = engine_class()
                logger.debug(f"Created engine instance: {engine_name}")
            except Exception as e:
                logger.error(f"Failed to create engine {engine_name}: {e}")
                raise

        return self._engines[engine_name]

    def _cache_key(
        self,
        title: str,
        description: Optional[str],
        translation_type: str,
        target_locale: str
    ) -> str:
        """Generate cache key for translation."""
        content = f"{title}|{description or ''}|{translation_type}|{target_locale}"
        return hashlib.sha256(content.encode()).hexdigest()

    async def translate_canonical(
        self,
        title: str,
        description: Optional[str],
        source_locale: str,
        source_platform: str = '',
        region: str = '',
    ) -> TranslationResult:
        """
        Translate to canonical locale (en-US) for ranking/analysis.

        Args:
            title: Original title
            description: Original description (optional)
            source_locale: Source locale code (e.g., 'ja-JP')
            source_platform: Platform name for context
            region: Region key for context

        Returns:
            TranslationResult with canonical translation
        """
        config = await self.get_config()
        target_locale = config.canonical_locale

        # Skip if already English
        if source_locale.startswith('en-'):
            logger.debug(f"Skipping canonical translation: source is already English ({source_locale})")
            return TranslationResult.no_translation_needed(
                title=title,
                description=description,
                source_locale=source_locale,
                target_locale=target_locale
            )

        # Check cache
        cache_key = self._cache_key(title, description, 'canonical', target_locale)
        if cache_key in self._cache:
            logger.debug("Returning cached canonical translation")
            return self._cache[cache_key]

        # Build context for prompt templates
        context = {
            'source_platform': source_platform,
            'region': region,
            'original_language': source_locale,
            'title': title,
            'description': description or '',
        }

        # Translate with fallback
        result = await self._translate_with_fallback(
            title=title,
            description=description,
            source_locale=source_locale,
            target_locale=target_locale,
            translation_type='canonical',
            context=context
        )

        # Cache successful result
        if result.success:
            self._cache[cache_key] = result

        return result

    async def translate_display(
        self,
        title: str,
        description: Optional[str],
        source_locale: str,
        target_locale: str,
        source_platform: str = '',
        region: str = '',
    ) -> TranslationResult:
        """
        Translate for human-readable UI display.

        Args:
            title: Original title
            description: Original description (optional)
            source_locale: Source locale code (e.g., 'ja-JP')
            target_locale: Target locale code (e.g., 'zh-Hans')
            source_platform: Platform name for context
            region: Region key for context

        Returns:
            TranslationResult with display translation
        """
        # Skip if same locale
        if source_locale == target_locale or (
            source_locale.startswith(target_locale[:2]) and
            target_locale.startswith(source_locale[:2])
        ):
            logger.debug(f"Skipping display translation: same locale ({source_locale} → {target_locale})")
            return TranslationResult.no_translation_needed(
                title=title,
                description=description,
                source_locale=source_locale,
                target_locale=target_locale
            )

        # Check cache
        cache_key = self._cache_key(title, description, 'display', target_locale)
        if cache_key in self._cache:
            logger.debug(f"Returning cached display translation for {target_locale}")
            return self._cache[cache_key]

        # Build context for prompt templates
        context = {
            'source_platform': source_platform,
            'region': region,
            'original_language': source_locale,
            'title': title,
            'description': description or '',
        }

        # Translate with fallback
        result = await self._translate_with_fallback(
            title=title,
            description=description,
            source_locale=source_locale,
            target_locale=target_locale,
            translation_type='display',
            context=context
        )

        # Cache successful result
        if result.success:
            self._cache[cache_key] = result

        return result

    async def _get_available_engines(self, translation_type: str) -> List[str]:
        """
        Get list of available engines filtered by health status.

        Args:
            translation_type: 'canonical' or 'display'

        Returns:
            List of engine names that are available and healthy
        """
        config = await self.get_config()
        all_engines = config.get_effective_fallback_order(translation_type)
        available_providers = await ProviderHealthManager.get_available_providers()

        # Filter to only available engines
        available_engines = [e for e in all_engines if e in available_providers]

        if not available_engines and all_engines:
            logger.warning(
                f"No healthy providers available. "
                f"Configured: {all_engines}, Available: {available_providers}"
            )

        return available_engines

    async def _translate_with_fallback(
        self,
        title: str,
        description: Optional[str],
        source_locale: str,
        target_locale: str,
        translation_type: str,
        context: Dict[str, Any],
    ) -> TranslationResult:
        """
        Translate with fallback through configured engines.

        Health-aware failover:
        - Checks system STOPPED state before processing
        - Filters to available (healthy) engines only
        - On quota/auth errors, marks provider unavailable and tries next
        - Returns special error codes for worker to handle appropriately

        Args:
            title: Text to translate
            description: Optional description
            source_locale: Source locale
            target_locale: Target locale
            translation_type: 'canonical' or 'display'
            context: Context for prompt templates

        Returns:
            TranslationResult from first successful engine
        """
        # 1. Check if system is STOPPED (all providers unavailable)
        if await ProviderHealthManager.is_stopped():
            logger.warning("System STOPPED: all providers unavailable")
            return TranslationResult.from_error(
                error="all_providers_unavailable",
                engine='system_stopped',
                source_locale=source_locale,
                target_locale=target_locale
            )

        # 2. Get available (healthy) engines
        engines = await self._get_available_engines(translation_type)
        if not engines:
            logger.warning("No available providers for translation")
            return TranslationResult.from_error(
                error="no_available_providers",
                engine='none_available',
                source_locale=source_locale,
                target_locale=target_locale
            )

        last_error = None

        # 3. Try each available engine
        for engine_name in engines:
            try:
                engine = self._get_engine(engine_name)

                # Check if engine supports this locale pair
                if not engine.supports_locale_pair(source_locale, target_locale):
                    logger.debug(
                        f"Engine {engine_name} doesn't support "
                        f"{source_locale} → {target_locale}, skipping"
                    )
                    continue

                logger.debug(
                    f"Trying {engine_name} for {translation_type} translation: "
                    f"{source_locale} → {target_locale}"
                )

                result = await engine.translate_item(
                    title=title,
                    description=description,
                    source_locale=source_locale,
                    target_locale=target_locale,
                    translation_type=translation_type,
                    context=context
                )

                if result.success:
                    # Mark provider as successful
                    await ProviderHealthManager.mark_success(engine_name)
                    logger.info(
                        f"Translation successful with {engine_name}: "
                        f"{source_locale} → {target_locale}"
                    )
                    return result

                # Engine returned error result (not exception)
                last_error = result.error
                logger.warning(
                    f"Engine {engine_name} failed: {result.error}, trying fallback"
                )

            except QuotaExceededError as e:
                # Quota/billing error - mark unavailable, immediate failover
                await ProviderHealthManager.handle_error(
                    engine_name, e, error_code='quota_exceeded'
                )
                last_error = str(e)
                logger.warning(
                    f"Engine {engine_name} quota exceeded, failover to next provider"
                )
                continue

            except AuthenticationError as e:
                # Authentication error - mark unavailable
                await ProviderHealthManager.handle_error(
                    engine_name, e, http_status=401
                )
                last_error = str(e)
                logger.warning(
                    f"Engine {engine_name} auth failed, failover to next provider"
                )
                continue

            except RateLimitError as e:
                # Rate limit - mark unavailable (will recover via health probes)
                await ProviderHealthManager.handle_error(
                    engine_name, e, http_status=429
                )
                last_error = str(e)
                logger.warning(
                    f"Engine {engine_name} rate limited, failover to next provider"
                )
                continue

            except TranslationError as e:
                last_error = str(e)
                logger.warning(
                    f"Engine {engine_name} raised error: {e}, trying fallback"
                )

            except Exception as e:
                last_error = str(e)
                logger.error(
                    f"Unexpected error with {engine_name}: {e}, trying fallback"
                )

        # All engines failed
        error_msg = f"All translation engines failed. Last error: {last_error}"
        logger.error(error_msg)

        return TranslationResult.from_error(
            error=error_msg,
            engine='all_failed',
            source_locale=source_locale,
            target_locale=target_locale
        )

    async def translate_item_canonical(self, item) -> TranslationResult:
        """
        Convenience method to translate a TrendItem to canonical locale.

        Args:
            item: TrendItem instance

        Returns:
            TranslationResult
        """
        return await self.translate_canonical(
            title=item.title_original,
            description=item.description_original,
            source_locale=item.original_locale,
            source_platform=item.surface.platform if item.surface else '',
            region=item.region.key if item.region else '',
        )

    async def translate_item_display(
        self,
        item,
        target_locale: str
    ) -> TranslationResult:
        """
        Convenience method to translate a TrendItem for display.

        Args:
            item: TrendItem instance
            target_locale: Target locale code

        Returns:
            TranslationResult
        """
        return await self.translate_display(
            title=item.title_original,
            description=item.description_original,
            source_locale=item.original_locale,
            target_locale=target_locale,
            source_platform=item.surface.platform if item.surface else '',
            region=item.region.key if item.region else '',
        )

    def clear_cache(self):
        """Clear in-memory translation cache."""
        self._cache.clear()
        logger.debug("Translation cache cleared")

    @property
    def cache_size(self) -> int:
        """Get current cache size."""
        return len(self._cache)

    async def close(self):
        """Clean up all engine resources."""
        for engine_name, engine in self._engines.items():
            try:
                await engine.close()
                logger.debug(f"Closed engine: {engine_name}")
            except Exception as e:
                logger.warning(f"Error closing engine {engine_name}: {e}")

        self._engines.clear()

    async def test_engine(self, engine_name: str) -> bool:
        """
        Test if an engine is working.

        Args:
            engine_name: Engine name to test

        Returns:
            True if engine is working
        """
        try:
            engine = self._get_engine(engine_name)
            result = await engine.translate(
                text="Hello",
                source_locale="en-US",
                target_locale="es-ES",
                translation_type="display"
            )
            logger.info(f"Engine {engine_name} test successful: 'Hello' → '{result}'")
            return True

        except Exception as e:
            logger.error(f"Engine {engine_name} test failed: {e}")
            return False

    async def test_all_engines(self) -> Dict[str, bool]:
        """
        Test all configured engines.

        Returns:
            Dict of engine_name → success boolean
        """
        config = await self.get_config()
        engines = config.fallback_order
        results = {}

        for engine_name in engines:
            results[engine_name] = await self.test_engine(engine_name)

        return results
