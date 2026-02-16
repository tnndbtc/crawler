"""
DeepL translation engine.

Uses the official DeepL SDK for high-quality machine translation.
"""

import asyncio
import logging
import os
from typing import Optional, Dict, Any

import deepl

from translation.base import (
    BaseTranslationEngine,
    TranslationError,
    QuotaExceededError,
    AuthenticationError,
)
from . import register_engine

logger = logging.getLogger(__name__)


@register_engine('deepl')
class DeepLEngine(BaseTranslationEngine):
    """
    DeepL translation engine using official SDK.

    Features:
    - Professional-grade translation quality
    - Fast and reliable
    - Support for 30+ languages
    - Character usage tracking
    - Proper quota exceeded error handling
    """

    # Locale mapping: our locale codes -> DeepL language codes
    LOCALE_MAP = {
        "en-US": "EN-US",
        "en-GB": "EN-GB",
        "zh-Hans": "ZH-HANS",
        "zh-Hant": "ZH-HANT",
        "es-ES": "ES",
        "fr-FR": "FR",
        "de-DE": "DE",
        "ja-JP": "JA",
        "ko-KR": "KO",
        "ru-RU": "RU",
        "ar-SA": "AR",
        "pt-PT": "PT-PT",
        "pt-BR": "PT-BR",
        "it-IT": "IT",
        "nl-NL": "NL",
        "pl-PL": "PL",
    }

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize DeepL engine.

        Args:
            api_key: DeepL API key (defaults to DEEPL_API_KEY env var)
        """
        self.api_key = api_key or os.getenv("DEEPL_API_KEY")
        if not self.api_key:
            raise ValueError("DeepL API key required (set DEEPL_API_KEY env var)")

        self._client = deepl.DeepLClient(self.api_key)

        # Log initialization
        key_prefix = self.api_key[:8] if len(self.api_key) >= 12 else "***"
        key_suffix = self.api_key[-4:] if len(self.api_key) >= 12 else "***"
        is_free_api = self.api_key.endswith(":fx")
        api_type = "Free" if is_free_api else "Pro"

        logger.info(
            f"Initialized DeepL engine | "
            f"API Key: {key_prefix}...{key_suffix} | "
            f"Type: {api_type}"
        )

    @property
    def name(self) -> str:
        return "deepl"

    def supports_locale_pair(self, source: str, target: str) -> bool:
        """Check if DeepL supports this locale pair."""
        # DeepL supports most major language pairs
        source_mapped = self._map_locale_source(source)
        target_mapped = self._map_locale_target(target)
        return source_mapped is not None and target_mapped is not None

    async def translate(
        self,
        text: str,
        source_locale: str,
        target_locale: str,
        translation_type: str = 'display',
        context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Translate text using DeepL.

        Args:
            text: Text to translate
            source_locale: Source locale code (e.g., "ja-JP")
            target_locale: Target locale code (e.g., "en-US")
            translation_type: Ignored for DeepL (no prompt customization)
            context: Ignored for DeepL

        Returns:
            Translated text

        Raises:
            TranslationError: If translation fails
            QuotaExceededError: If API quota is exceeded
        """
        if not text or not text.strip():
            return text

        source_lang = self._map_locale_source(source_locale)
        target_lang = self._map_locale_target(target_locale)

        try:
            # DeepL SDK is synchronous, run in executor
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._client.translate_text(
                    text,
                    source_lang=source_lang,
                    target_lang=target_lang
                )
            )
            return result.text.strip()

        except deepl.QuotaExceededException as e:
            logger.error(f"DeepL quota exceeded: {e}")
            raise QuotaExceededError(f"DeepL API quota exceeded: {e}", engine=self.name)

        except deepl.AuthorizationException as e:
            logger.error(f"DeepL authentication failed: {e}")
            raise AuthenticationError(f"DeepL authentication failed: {e}", engine=self.name)

        except deepl.DeepLException as e:
            logger.error(f"DeepL API error: {e}")
            raise TranslationError(f"DeepL API error: {e}", engine=self.name)

        except Exception as e:
            logger.error(f"DeepL translation failed: {e}")
            raise TranslationError(f"DeepL translation failed: {e}", engine=self.name)

    def _map_locale_source(self, locale: str) -> Optional[str]:
        """
        Map locale to DeepL SOURCE language code.

        DeepL source_lang only accepts base codes (EN, JA, ZH, etc.)
        NOT variant codes (EN-US, EN-GB are invalid for source).
        """
        deepl_lang = self.LOCALE_MAP.get(locale)

        if not deepl_lang:
            # Fallback: use first 2 characters uppercase
            deepl_lang = locale[:2].upper()
            logger.warning(f"Locale {locale} not in DeepL map, using fallback: {deepl_lang}")

        # For source_lang, strip variant suffix (EN-US → EN, PT-BR → PT)
        base_code = deepl_lang.split('-')[0]
        return base_code

    def _map_locale_target(self, locale: str) -> Optional[str]:
        """
        Map locale to DeepL TARGET language code.

        DeepL target_lang accepts variant codes (EN-US, EN-GB, PT-BR, etc.)
        for languages that have variants.
        """
        deepl_lang = self.LOCALE_MAP.get(locale)

        if not deepl_lang:
            # Fallback: use first 2 characters uppercase
            deepl_lang = locale[:2].upper()
            logger.warning(f"Locale {locale} not in DeepL map, using fallback: {deepl_lang}")

        return deepl_lang

    async def get_usage(self):
        """Get current DeepL API usage statistics."""
        try:
            loop = asyncio.get_event_loop()
            usage = await loop.run_in_executor(None, self._client.get_usage)
            return usage
        except Exception as e:
            logger.warning(f"Failed to get DeepL usage stats: {e}")
            return None

    async def close(self):
        """Close DeepL client (no-op for SDK)."""
        pass
