"""
Translation service providers.

Adapted from /trend/trend_agent/services/translation.py
Simplified for MVP with DeepL and OpenAI support.
"""

import asyncio
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class TranslationError(Exception):
    """Raised when translation fails."""
    pass


class OpenAITranslationProvider:
    """
    OpenAI GPT-based translation provider.

    Features:
    - High-quality, context-aware translation
    - Support for 50+ languages
    - Automatic language detection
    - Retry logic with exponential backoff

    Uses environment variable: OPENAI_API_KEY
    """

    API_ENDPOINT = "https://api.openai.com/v1/chat/completions"
    DEFAULT_MODEL = "gpt-3.5-turbo"  # Cheaper and faster for translation

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        max_retries: int = 3,
        timeout: int = 30,
    ):
        """
        Initialize OpenAI translation provider.

        Args:
            api_key: OpenAI API key (defaults to OPENAI_API_KEY env var)
            model: Model to use (gpt-3.5-turbo or gpt-4)
            max_retries: Maximum retry attempts
            timeout: Request timeout in seconds
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key required (set OPENAI_API_KEY env var)")

        self.model = model
        self.max_retries = max_retries
        self.timeout = timeout

        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

        logger.info(f"Initialized OpenAI translation provider (model={model})")

    async def translate(
        self,
        text: str,
        source_locale: str,
        target_locale: str,
    ) -> str:
        """
        Translate text from source to target locale.

        Args:
            text: Text to translate
            source_locale: Source locale code (e.g., "ja-JP")
            target_locale: Target locale code (e.g., "en-US")

        Returns:
            Translated text

        Raises:
            TranslationError: If translation fails
        """
        if not text or not text.strip():
            return text

        instruction = (
            f"Translate the following text from {source_locale} to {target_locale}. "
            f"Preserve formatting, tone, and meaning. Return only the translated text."
        )

        try:
            result = await self._call_api(instruction, text)
            return result.strip()
        except Exception as e:
            logger.error(f"OpenAI translation failed: {e}")
            raise TranslationError(f"OpenAI translation failed: {e}") from e

    async def _call_api(self, system_instruction: str, user_content: str) -> str:
        """Call OpenAI API with retry logic."""
        last_error = None

        for attempt in range(self.max_retries):
            try:
                payload = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_instruction},
                        {"role": "user", "content": user_content},
                    ],
                    "temperature": 0.3,  # Low temperature for consistent translation
                }

                response = await self._client.post(self.API_ENDPOINT, json=payload)
                response.raise_for_status()

                data = response.json()
                return data["choices"][0]["message"]["content"]

            except httpx.HTTPStatusError as e:
                last_error = e
                status = e.response.status_code

                # Don't retry client errors (except rate limits)
                if 400 <= status < 500 and status != 429:
                    raise TranslationError(f"OpenAI API error: {e.response.text}") from e

                logger.warning(
                    f"API error (attempt {attempt + 1}/{self.max_retries}): "
                    f"{status} - {e.response.text}"
                )

                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff

            except Exception as e:
                last_error = e
                logger.warning(
                    f"Unexpected error (attempt {attempt + 1}/{self.max_retries}): {e}"
                )

                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)

        raise TranslationError(f"All retries failed: {last_error}") from last_error

    async def close(self):
        """Close HTTP client."""
        await self._client.aclose()


class DeepLTranslationProvider:
    """
    DeepL translation provider.

    Features:
    - Professional-grade translation quality
    - Fast and reliable
    - Support for 30+ languages
    - Character usage tracking

    Uses environment variable: DEEPL_API_KEY
    """

    API_ENDPOINT = "https://api-free.deepl.com/v2/translate"

    # Locale mapping: our locale codes -> DeepL language codes
    LOCALE_MAP = {
        "en-US": "EN-US",
        "en-GB": "EN-GB",
        "zh-Hans": "ZH",
        "zh-Hant": "ZH",  # DeepL doesn't distinguish traditional/simplified
        "es-ES": "ES",
        "fr-FR": "FR",
        "de-DE": "DE",
        "ja-JP": "JA",
        "ko-KR": "KO",  # Note: DeepL may not support Korean yet
        "ru-RU": "RU",
        "ar-SA": "AR",  # Note: DeepL may not support Arabic yet
        "pt-PT": "PT-PT",
        "pt-BR": "PT-BR",
        "it-IT": "IT",
        "nl-NL": "NL",
        "pl-PL": "PL",
    }

    def __init__(
        self,
        api_key: Optional[str] = None,
        max_retries: int = 3,
        timeout: int = 30,
    ):
        """
        Initialize DeepL translation provider.

        Args:
            api_key: DeepL API key (defaults to DEEPL_API_KEY env var)
            max_retries: Maximum retry attempts
            timeout: Request timeout in seconds
        """
        self.api_key = api_key or os.getenv("DEEPL_API_KEY")
        if not self.api_key:
            raise ValueError("DeepL API key required (set DEEPL_API_KEY env var)")

        self.max_retries = max_retries
        self.timeout = timeout

        self._client = httpx.AsyncClient(timeout=timeout)

        logger.info("Initialized DeepL translation provider")

    async def translate(
        self,
        text: str,
        source_locale: str,
        target_locale: str,
    ) -> str:
        """
        Translate text from source to target locale.

        Args:
            text: Text to translate
            source_locale: Source locale code (e.g., "ja-JP")
            target_locale: Target locale code (e.g., "en-US")

        Returns:
            Translated text

        Raises:
            TranslationError: If translation fails
        """
        if not text or not text.strip():
            return text

        # Map our locales to DeepL language codes
        source_lang = self._map_locale(source_locale)
        target_lang = self._map_locale(target_locale)

        try:
            result = await self._call_api(text, source_lang, target_lang)
            return result.strip()
        except Exception as e:
            logger.error(f"DeepL translation failed: {e}")
            raise TranslationError(f"DeepL translation failed: {e}") from e

    def _map_locale(self, locale: str) -> str:
        """
        Map our locale code to DeepL language code.

        Args:
            locale: Our locale code (e.g., "en-US", "ja-JP")

        Returns:
            DeepL language code (e.g., "EN-US", "JA")
        """
        deepl_lang = self.LOCALE_MAP.get(locale)

        if not deepl_lang:
            # Fallback: use first 2 characters uppercase
            deepl_lang = locale[:2].upper()
            logger.warning(
                f"Locale {locale} not in DeepL map, using fallback: {deepl_lang}"
            )

        return deepl_lang

    async def _call_api(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
    ) -> str:
        """Call DeepL API with retry logic."""
        last_error = None

        for attempt in range(self.max_retries):
            try:
                data = {
                    "auth_key": self.api_key,
                    "text": text,
                    "source_lang": source_lang,
                    "target_lang": target_lang,
                }

                response = await self._client.post(self.API_ENDPOINT, data=data)
                response.raise_for_status()

                result = response.json()
                return result["translations"][0]["text"]

            except httpx.HTTPStatusError as e:
                last_error = e
                status = e.response.status_code

                # Don't retry client errors (except rate limits)
                if 400 <= status < 500 and status != 429:
                    raise TranslationError(f"DeepL API error: {e.response.text}") from e

                logger.warning(
                    f"API error (attempt {attempt + 1}/{self.max_retries}): "
                    f"{status} - {e.response.text}"
                )

                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)

            except Exception as e:
                last_error = e
                logger.warning(
                    f"Unexpected error (attempt {attempt + 1}/{self.max_retries}): {e}"
                )

                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)

        raise TranslationError(f"All retries failed: {last_error}") from last_error

    async def close(self):
        """Close HTTP client."""
        await self._client.aclose()


def get_provider(provider_name: str):
    """
    Get translation provider by name.

    Args:
        provider_name: Provider name ("deepl" or "openai")

    Returns:
        Translation provider instance

    Raises:
        ValueError: If provider name is unknown
    """
    if provider_name == "deepl":
        return DeepLTranslationProvider()
    elif provider_name == "openai":
        return OpenAITranslationProvider()
    else:
        raise ValueError(f"Unknown translation provider: {provider_name}")
