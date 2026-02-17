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
    - Admin-configurable prompts and model params via LLMModelConfig
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

        # Log masked API key for debugging
        masked_key = self._mask_api_key(self.api_key)
        logger.info(f"Initialized OpenAI engine (model override: {model or 'from config'}, api_key: {masked_key})")

    def _mask_api_key(self, key: str) -> str:
        """Mask API key for safe logging, showing first 7 and last 4 chars."""
        if not key:
            return "(not set)"
        if len(key) <= 11:
            return "***"
        return f"{key[:7]}...{key[-4:]}"

    @property
    def name(self) -> str:
        return "openai"

    def supports_locale_pair(self, source: str, target: str) -> bool:
        """OpenAI GPT supports all language pairs."""
        return True

    async def _get_llm_model(self, translation_type: str):
        """
        Get LLM model config from database based on translation type.

        Args:
            translation_type: 'canonical' or 'display'

        Returns:
            LLMModelConfig instance or None
        """
        try:
            from asgiref.sync import sync_to_async
            from translation.models import TranslationConfig

            @sync_to_async
            def get_model():
                config = TranslationConfig.get_config()
                model = config.get_llm_model(translation_type)
                if model:
                    # Pre-fetch all needed attributes while in sync context
                    return {
                        'model_id': model.model_id,
                        'temperature': model.temperature,
                        'top_p': model.top_p,
                        'max_tokens': model.max_tokens,
                        'system_prompt': model.system_prompt,
                        'developer_prompt': model.developer_prompt,
                        'user_prompt': model.user_prompt,
                    }
                return None

            return await get_model()
        except Exception as e:
            logger.warning(f"Failed to load LLM model config: {e}")
            return None

    def _get_model_params_from_config(self, model_config: Optional[dict]) -> tuple:
        """
        Get model parameters from config dict or defaults.

        Args:
            model_config: Dict with model params or None

        Returns:
            (model_id, temperature, top_p, max_tokens)
        """
        if self._model_override:
            return (self._model_override, self.DEFAULT_TEMPERATURE, self.DEFAULT_TOP_P, self.DEFAULT_MAX_TOKENS)

        if model_config:
            return (
                model_config['model_id'],
                model_config['temperature'],
                model_config['top_p'],
                model_config['max_tokens']
            )

        return (self.DEFAULT_MODEL, self.DEFAULT_TEMPERATURE, self.DEFAULT_TOP_P, self.DEFAULT_MAX_TOKENS)

    def _render_prompts(self, model_config: dict, context: dict) -> dict:
        """
        Render prompts with context variables.

        Args:
            model_config: Dict with prompt templates
            context: Dict with variable values

        Returns:
            Dict with rendered 'system', 'developer', and 'user' prompts
        """
        def substitute(template: str, ctx: dict) -> str:
            if not template:
                return ''
            result = template
            for key, value in ctx.items():
                result = result.replace(f'{{{key}}}', str(value) if value else '')
            return result

        return {
            'system': substitute(model_config['system_prompt'], context),
            'developer': substitute(model_config['developer_prompt'], context) if model_config['developer_prompt'] else None,
            'user': substitute(model_config['user_prompt'], context),
        }

    def _build_messages(
        self,
        text: str,
        source_locale: str,
        target_locale: str,
        translation_type: str,
        context: Optional[Dict[str, Any]] = None,
        model_config: Optional[dict] = None
    ) -> list:
        """
        Build chat messages for translation.

        Uses prompts from the selected LLMModelConfig if available,
        otherwise falls back to hardcoded prompts.
        """
        if model_config and model_config.get('system_prompt'):
            # Build context for prompt rendering
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

            rendered = self._render_prompts(model_config, render_context)

            messages = [{"role": "system", "content": rendered['system']}]

            if rendered.get('developer'):
                messages.append({"role": "developer", "content": rendered['developer']})

            messages.append({"role": "user", "content": rendered['user']})

            return messages

        # Fallback: hardcoded prompts (if model config unavailable or has no prompts)
        if translation_type == 'canonical':
            system_prompt = (
                "You are a semantic normalization engine. "
                "Produce stable, literal, machine-consistent English meaning. "
                "Return only the normalized sentence(s)."
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

        # Fetch model config asynchronously
        model_config = await self._get_llm_model(translation_type)

        model_id, temperature, top_p, max_tokens = self._get_model_params_from_config(model_config)
        messages = self._build_messages(
            text, source_locale, target_locale, translation_type, context, model_config
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
            # Fetch model config asynchronously
            model_config = await self._get_llm_model(translation_type)

            model_id, temperature, top_p, max_tokens = self._get_model_params_from_config(model_config)
            messages = self._build_messages(
                combined, source_locale, target_locale, translation_type, ctx, model_config
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
