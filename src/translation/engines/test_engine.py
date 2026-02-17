"""
Test Translation Engine - Deterministic translator for dev/test environments.

This engine provides deterministic translations without requiring external API keys.
Useful for:
- Development environments without API credentials
- Integration testing of the full translation pipeline
- Validating ItemDerivation creation without API costs

Translation format: "[{target_locale}] {original_text}"
Example: "Hello" (en-US → zh-Hans) → "[zh-Hans] Hello"
"""

import logging
from typing import Dict, Any, Optional

from translation.base import BaseTranslationEngine, TranslationError
from translation.engines import register_engine

logger = logging.getLogger(__name__)


@register_engine('test-fallback')
class DeterministicTestEngine(BaseTranslationEngine):
    """
    Deterministic test translator that prefixes text with target locale.

    This engine is automatically registered when TRANSLATION_TEST_MODE=true
    and no valid API keys are configured.

    Properties:
    - Deterministic: Same input always produces same output
    - No external dependencies: Works without API keys
    - Creates real ItemDerivation rows with status='complete'
    - Supports all locale pairs
    """

    @property
    def name(self) -> str:
        """Engine name for logging and attribution."""
        return 'test-fallback'

    async def translate(
        self,
        text: str,
        source_locale: str,
        target_locale: str,
        translation_type: str = 'display',
        context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Translate text by prefixing with target locale.

        Args:
            text: Text to translate
            source_locale: Source locale code (e.g., "en-US")
            target_locale: Target locale code (e.g., "zh-Hans")
            translation_type: 'canonical' or 'display'
            context: Additional context (ignored)

        Returns:
            Translated text in format: "[{target_locale}] {text}"

        Raises:
            TranslationError: If text is empty
        """
        if not text:
            raise TranslationError(
                "Cannot translate empty text",
                engine=self.name
            )

        # Deterministic transform: prefix with target locale
        translated = f"[{target_locale}] {text}"

        logger.debug(
            f"Test translation: '{text}' ({source_locale} → {target_locale}) = '{translated}'"
        )

        return translated

    def supports_locale_pair(self, source: str, target: str) -> bool:
        """
        Check if this engine supports the locale pair.

        The test engine supports all locale pairs for maximum flexibility.

        Args:
            source: Source locale code
            target: Target locale code

        Returns:
            Always True (supports all pairs)
        """
        return True
