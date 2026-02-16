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
import deepl
import argostranslate.package
import argostranslate.translate

logger = logging.getLogger(__name__)


class TranslationError(Exception):
    """Raised when translation fails."""
    pass


class DeepLQuotaExceededError(TranslationError):
    """Raised when DeepL API quota is exceeded."""
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
    DeepL translation provider using official deepl-python SDK.

    Features:
    - Professional-grade translation quality
    - Fast and reliable
    - Support for 30+ languages
    - Character usage tracking
    - Proper quota exceeded error handling

    Uses environment variable: DEEPL_API_KEY
    """

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
        "ko-KR": "KO",
        "ru-RU": "RU",
        "ar-SA": "AR",
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
            max_retries: Maximum retry attempts (not used with SDK, kept for compatibility)
            timeout: Request timeout in seconds (not used with SDK, kept for compatibility)
        """
        self.api_key = api_key or os.getenv("DEEPL_API_KEY")
        if not self.api_key:
            raise ValueError("DeepL API key required (set DEEPL_API_KEY env var)")

        self.max_retries = max_retries
        self.timeout = timeout

        # Initialize the official DeepL SDK client
        self._client = deepl.DeepLClient(self.api_key)

        logger.info("Initialized DeepL translation provider (using official SDK)")

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
            DeepLQuotaExceededError: If API quota is exceeded
            TranslationError: If translation fails for other reasons
        """
        if not text or not text.strip():
            return text

        # Map our locales to DeepL language codes
        source_lang = self._map_locale(source_locale)
        target_lang = self._map_locale(target_locale)

        try:
            # DeepL SDK is synchronous, so run in executor
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
            raise DeepLQuotaExceededError(f"DeepL API quota exceeded: {e}") from e
        except deepl.DeepLException as e:
            logger.error(f"DeepL API error: {e}")
            raise TranslationError(f"DeepL API error: {e}") from e
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

    async def get_usage(self):
        """
        Get current DeepL API usage statistics.

        Returns:
            Usage object with character count and limit information
        """
        try:
            loop = asyncio.get_event_loop()
            usage = await loop.run_in_executor(None, self._client.get_usage)
            return usage
        except Exception as e:
            logger.warning(f"Failed to get DeepL usage stats: {e}")
            return None

    async def close(self):
        """Close DeepL translator (no-op for SDK)."""
        # The SDK doesn't require explicit cleanup
        pass


class LocalTranslationProvider:
    """
    Local offline translation provider using argostranslate.

    Features:
    - Completely offline (no API key required)
    - No quota limits
    - Free and open source
    - Lower quality than DeepL/OpenAI
    - Requires downloading language models (~100-500MB per language pair)

    Use as fallback when DeepL quota is exceeded.
    """

    # Locale mapping: our locale codes -> argostranslate language codes
    LOCALE_MAP = {
        "en-US": "en",
        "en-GB": "en",
        "zh-Hans": "zh",
        "zh-Hant": "zh",
        "es-ES": "es",
        "fr-FR": "fr",
        "de-DE": "de",
        "ja-JP": "ja",
        "ko-KR": "ko",
        "ru-RU": "ru",
        "ar-SA": "ar",
        "pt-PT": "pt",
        "pt-BR": "pt",
        "it-IT": "it",
        "nl-NL": "nl",
        "pl-PL": "pl",
    }

    _models_installed = False

    def __init__(self):
        """Initialize local translation provider."""
        self._ensure_models_installed()
        logger.info("Initialized local offline translation provider (argostranslate)")

    def _ensure_models_installed(self):
        """Ensure required translation models are installed."""
        if LocalTranslationProvider._models_installed:
            return

        try:
            # Update package index
            argostranslate.package.update_package_index()
            available_packages = argostranslate.package.get_available_packages()

            # Get currently installed packages
            installed_languages = argostranslate.translate.get_installed_languages()
            installed_codes = {(lang.code,) for lang in installed_languages}

            # Install common language pairs to/from English
            # This will download models on first run
            common_langs = ["ja", "zh", "ko", "es", "fr", "de", "ru", "ar", "pt", "it"]

            installed_count = 0
            for lang in common_langs:
                # Install lang -> en
                pkg_to_en = next(
                    (p for p in available_packages
                     if p.from_code == lang and p.to_code == "en"),
                    None
                )
                if pkg_to_en:
                    # Check if already installed by checking if both languages exist
                    if lang not in [l.code for l in installed_languages]:
                        logger.info(f"Installing translation model: {lang} -> en")
                        download_path = pkg_to_en.download()
                        argostranslate.package.install_from_path(download_path)
                        installed_count += 1
                    else:
                        logger.debug(f"Model {lang} -> en already installed")

                # Install en -> lang (for reverse translation if needed)
                pkg_from_en = next(
                    (p for p in available_packages
                     if p.from_code == "en" and p.to_code == lang),
                    None
                )
                if pkg_from_en:
                    if "en" not in [l.code for l in installed_languages]:
                        logger.info(f"Installing translation model: en -> {lang}")
                        download_path = pkg_from_en.download()
                        argostranslate.package.install_from_path(download_path)
                        installed_count += 1
                    else:
                        logger.debug(f"Model en -> {lang} already installed")

            if installed_count > 0:
                logger.info(f"Installed {installed_count} translation models")
            else:
                logger.info("All required translation models already installed")

            LocalTranslationProvider._models_installed = True

        except Exception as e:
            logger.warning(f"Failed to install some translation models: {e}")
            # Continue anyway - we'll handle missing models during translation

    async def translate(
        self,
        text: str,
        source_locale: str,
        target_locale: str,
    ) -> str:
        """
        Translate text from source to target locale using offline models.

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

        # Map our locales to argostranslate language codes
        source_lang = self._map_locale(source_locale)
        target_lang = self._map_locale(target_locale)

        try:
            # argostranslate is CPU-bound, run in executor
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: argostranslate.translate.translate(
                    text,
                    source_lang,
                    target_lang
                )
            )
            return result.strip() if result else text
        except Exception as e:
            logger.error(f"Local translation failed: {e}")
            raise TranslationError(f"Local translation failed: {e}") from e

    def _map_locale(self, locale: str) -> str:
        """
        Map our locale code to argostranslate language code.

        Args:
            locale: Our locale code (e.g., "en-US", "ja-JP")

        Returns:
            argostranslate language code (e.g., "en", "ja")
        """
        lang = self.LOCALE_MAP.get(locale)

        if not lang:
            # Fallback: use first 2 characters lowercase
            lang = locale[:2].lower()
            logger.warning(
                f"Locale {locale} not in local map, using fallback: {lang}"
            )

        return lang

    async def close(self):
        """Close local translator (no-op)."""
        pass


def get_provider(provider_name: str):
    """
    Get translation provider by name.

    Args:
        provider_name: Provider name ("deepl", "openai", "argostranslate")
                      Legacy: "local" is supported as alias for "argostranslate"

    Returns:
        Translation provider instance

    Raises:
        ValueError: If provider name is unknown
    """
    if provider_name == "deepl":
        return DeepLTranslationProvider()
    elif provider_name == "openai":
        return OpenAITranslationProvider()
    elif provider_name in ("argostranslate", "local"):  # "local" is legacy alias
        return LocalTranslationProvider()
    else:
        raise ValueError(f"Unknown translation provider: {provider_name}")
