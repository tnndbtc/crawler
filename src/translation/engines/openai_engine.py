"""
OpenAI translation engine with admin-managed prompts.

Uses OpenAI GPT models for context-aware, high-quality translation.
Prompts are loaded from the database and can be edited via admin.

Provider Health Integration:
- Detects billing/quota errors and raises QuotaExceededError
- Reduces retry attempts for quick failover to next provider
"""

import asyncio
import json
import logging
import os
from typing import Optional, Dict, Any

import httpx

from translation.base import (
    BaseTranslationEngine,
    TranslationResult,
    TranslationError,
    AuthenticationError,
    RateLimitError,
    QuotaExceededError,
)
from . import register_engine

logger = logging.getLogger(__name__)

# OpenAI error codes indicating billing/quota issues
OPENAI_QUOTA_ERROR_CODES = {
    'insufficient_quota',
    'billing_hard_limit_reached',
    'billing_not_active',
    'quota_exceeded',
}


@register_engine('openai')
class OpenAIEngine(BaseTranslationEngine):
    """
    OpenAI GPT-based translation engine with prompt templates.

    Features:
    - High-quality, context-aware translation
    - Support for 50+ languages
    - Admin-configurable prompts via PromptTemplate model
    - Admin-configurable models via LLMModelConfig model
    - Retry logic with exponential backoff
    """

    API_ENDPOINT = "https://api.openai.com/v1/chat/completions"
    DEFAULT_MODEL = "gpt-4o-mini"
    DEFAULT_TEMPERATURE = 0.3
    DEFAULT_TOP_P = 1.0
    DEFAULT_MAX_TOKENS = 1000

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_retries: int = 1,
        timeout: int = 30,
    ):
        """
        Initialize OpenAI engine.

        Args:
            api_key: OpenAI API key (defaults to OPENAI_API_KEY env var)
            model: Model to use (can be overridden by LLMModelConfig)
            max_retries: Maximum retry attempts (default 1 for quick failover)
            timeout: Request timeout in seconds

        Note: max_retries reduced to 1 for quick failover to alternative providers.
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key required (set OPENAI_API_KEY env var)")

        self._model_override = model
        self.max_retries = max_retries
        self.timeout = timeout

        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

        logger.info(f"Initialized OpenAI engine (model override: {model or 'from config'})")

    @property
    def name(self) -> str:
        return "openai"

    def supports_locale_pair(self, source: str, target: str) -> bool:
        """OpenAI GPT supports all language pairs."""
        return True

    def _get_model_config(self) -> tuple:
        """
        Get model configuration from database or defaults.

        Returns:
            (model_id, temperature, top_p, max_tokens)
        """
        if self._model_override:
            return (self._model_override, self.DEFAULT_TEMPERATURE, self.DEFAULT_TOP_P, self.DEFAULT_MAX_TOKENS)

        try:
            from translation.models import LLMModelConfig
            config = LLMModelConfig.get_default(provider='openai')
            if config:
                top_p = getattr(config, 'top_p', self.DEFAULT_TOP_P)
                return (config.model_id, config.temperature, top_p, config.max_tokens)
        except Exception as e:
            logger.warning(f"Failed to load LLM model config: {e}")

        return (self.DEFAULT_MODEL, self.DEFAULT_TEMPERATURE, self.DEFAULT_TOP_P, self.DEFAULT_MAX_TOKENS)

    def _get_prompt_template(self, translation_type: str):
        """
        Get prompt template from database.

        Args:
            translation_type: 'canonical' or 'display'

        Returns:
            PromptTemplate instance or None
        """
        try:
            from translation.models import PromptTemplate
            return PromptTemplate.get_default(translation_type)
        except Exception as e:
            logger.warning(f"Failed to load prompt template: {e}")
            return None

    def _build_messages(
        self,
        text: str,
        source_locale: str,
        target_locale: str,
        translation_type: str,
        context: Optional[Dict[str, Any]] = None
    ) -> list:
        """
        Build chat messages for translation.

        Uses prompt templates from database if available,
        otherwise falls back to hardcoded prompts.
        """
        # Try to load template from database
        template = self._get_prompt_template(translation_type)

        if template:
            # Build context for template
            render_context = {
                'source_platform': context.get('source_platform', '') if context else '',
                'region': context.get('region', '') if context else '',
                'original_language': source_locale,
                'title': context.get('title', text) if context else text,
                'description': context.get('description', '') if context else '',
                'text': text,
                'source_locale': source_locale,
                'target_locale': target_locale,
            }

            rendered = template.render(render_context)

            messages = [{"role": "system", "content": rendered['system']}]

            if rendered.get('developer'):
                messages.append({"role": "developer", "content": rendered['developer']})

            messages.append({"role": "user", "content": rendered['user']})

            return messages

        # Fallback: hardcoded prompts
        if translation_type == 'canonical':
            system_prompt = (
                "You are a semantic normalization translator. "
                "Translate to English (en-US) preserving exact meaning. "
                "Normalize formatting for text similarity comparison. "
                "Output only the translated text, no explanations."
            )
            user_prompt = f"Translate this {source_locale} text to normalized English:\n\n{text}"
        else:
            system_prompt = (
                "You are a professional translator. "
                "Translate naturally while preserving tone and cultural nuances. "
                "Output only the translated text, no explanations."
            )
            user_prompt = f"Translate this {source_locale} text to {target_locale}:\n\n{text}"

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    async def translate(
        self,
        text: str,
        source_locale: str,
        target_locale: str,
        translation_type: str = 'display',
        context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Translate text using OpenAI GPT.

        Args:
            text: Text to translate
            source_locale: Source locale code (e.g., "ja-JP")
            target_locale: Target locale code (e.g., "en-US")
            translation_type: 'canonical' or 'display'
            context: Additional context for prompt rendering

        Returns:
            Translated text

        Raises:
            TranslationError: If translation fails
        """
        if not text or not text.strip():
            return text

        model_id, temperature, top_p, max_tokens = self._get_model_config()
        messages = self._build_messages(
            text, source_locale, target_locale, translation_type, context
        )

        try:
            result = await self._call_api(messages, model_id, temperature, top_p, max_tokens)
            return result.strip()
        except Exception as e:
            logger.error(f"OpenAI translation failed: {e}")
            raise TranslationError(f"OpenAI translation failed: {e}", engine=self.name)

    async def _call_api(
        self,
        messages: list,
        model: str,
        temperature: float,
        top_p: float,
        max_tokens: int
    ) -> str:
        """Call OpenAI API with retry logic."""
        last_error = None

        for attempt in range(self.max_retries):
            try:
                payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "top_p": top_p,
                    "max_tokens": max_tokens,
                }

                response = await self._client.post(self.API_ENDPOINT, json=payload)
                response.raise_for_status()

                data = response.json()
                return data["choices"][0]["message"]["content"]

            except httpx.HTTPStatusError as e:
                last_error = e
                status = e.response.status_code

                # Parse error response to get error code
                error_code = self._parse_error_code(e.response)

                # Check for billing/quota errors (any status code)
                if error_code and error_code in OPENAI_QUOTA_ERROR_CODES:
                    logger.error(
                        f"OpenAI quota/billing error detected: code={error_code}"
                    )
                    raise QuotaExceededError(
                        f"OpenAI quota exceeded: {error_code}",
                        engine=self.name
                    )

                # Handle specific HTTP status codes
                if status == 401:
                    raise AuthenticationError(
                        f"OpenAI authentication failed: {e.response.text}",
                        engine=self.name
                    )

                if status == 429:
                    # Check if this is a billing-related 429
                    if error_code and 'quota' in error_code.lower():
                        raise QuotaExceededError(
                            f"OpenAI quota exceeded (429): {error_code}",
                            engine=self.name
                        )

                    # Rate limit - reduced retries for quick failover
                    retry_after = e.response.headers.get('retry-after')
                    retry_seconds = int(retry_after) if retry_after else (2 ** attempt)
                    logger.warning(
                        f"OpenAI rate limit hit (attempt {attempt + 1}/{self.max_retries}), "
                        f"retrying in {retry_seconds}s"
                    )
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(retry_seconds)
                        continue
                    raise RateLimitError(
                        f"OpenAI rate limit exceeded: {e.response.text}",
                        engine=self.name,
                        retry_after=retry_seconds
                    )

                # Don't retry other client errors
                if 400 <= status < 500:
                    raise TranslationError(
                        f"OpenAI API error: {e.response.text}",
                        engine=self.name
                    )

                # Retry server errors
                logger.warning(
                    f"OpenAI server error (attempt {attempt + 1}/{self.max_retries}): "
                    f"{status} - {e.response.text}"
                )
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)

            except Exception as e:
                last_error = e
                logger.warning(
                    f"OpenAI unexpected error (attempt {attempt + 1}/{self.max_retries}): {e}"
                )
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)

        raise TranslationError(
            f"OpenAI all retries failed: {last_error}",
            engine=self.name
        )

    def _parse_error_code(self, response: httpx.Response) -> Optional[str]:
        """
        Parse OpenAI error response to extract error code.

        OpenAI error responses have format:
        {
            "error": {
                "message": "...",
                "type": "...",
                "code": "insufficient_quota"
            }
        }

        Returns:
            Error code string or None if not found
        """
        try:
            data = response.json()
            if isinstance(data, dict) and 'error' in data:
                error_obj = data['error']
                if isinstance(error_obj, dict):
                    return error_obj.get('code')
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
        return None

    async def translate_item(
        self,
        title: str,
        description: Optional[str],
        source_locale: str,
        target_locale: str,
        translation_type: str = 'display',
        context: Optional[Dict[str, Any]] = None
    ) -> TranslationResult:
        """
        Translate item with optimized single API call.

        For OpenAI, we can translate title and description together
        in a single API call for efficiency.
        """
        # Build combined text
        combined = f"Title: {title}"
        if description:
            combined += f"\nDescription: {description}"

        # Update context
        ctx = context.copy() if context else {}
        ctx['title'] = title
        ctx['description'] = description or ''

        try:
            model_id, temperature, top_p, max_tokens = self._get_model_config()
            messages = self._build_messages(
                combined, source_locale, target_locale, translation_type, ctx
            )

            result = await self._call_api(messages, model_id, temperature, top_p, max_tokens)

            # Parse response
            translated_title, translated_description = self._parse_response(result)

            return TranslationResult(
                title=translated_title,
                description=translated_description,
                engine=self.name,
                source_locale=source_locale,
                target_locale=target_locale,
                metadata={'model': model_id}
            )

        except Exception as e:
            return TranslationResult.from_error(
                error=str(e),
                engine=self.name,
                source_locale=source_locale,
                target_locale=target_locale
            )

    def _parse_response(self, response: str) -> tuple:
        """
        Parse combined title/description response.

        Expected format:
        Title: <translated title>
        Description: <translated description>

        Returns:
            (title, description) tuple
        """
        lines = response.strip().split('\n')
        title = ''
        description = None

        for line in lines:
            line = line.strip()
            if line.lower().startswith('title:'):
                title = line[6:].strip()
            elif line.lower().startswith('description:'):
                description = line[12:].strip()

        # If parsing failed, treat entire response as title
        if not title:
            title = response.strip()

        return (title, description)

    async def close(self):
        """Close HTTP client."""
        await self._client.aclose()
