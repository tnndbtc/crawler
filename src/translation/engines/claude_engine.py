"""
Claude CLI translation engine.

Uses `claude -p` subprocess for translation via Anthropic's Claude CLI.
No API key needed — relies on the CLI being installed and authenticated.
"""

import asyncio
import json
import logging
import re
from typing import Optional, Dict, Any

from translation.base import (
    BaseTranslationEngine,
    TranslationResult,
    TranslationError,
    AuthenticationError,
    RateLimitError,
)
from . import register_engine

logger = logging.getLogger(__name__)

# Timeout for each `claude -p` subprocess call (seconds)
CLAUDE_CLI_TIMEOUT = 60


@register_engine('claude')
class ClaudeEngine(BaseTranslationEngine):
    """
    Claude CLI translation engine.

    Uses `claude -p` to translate text via subprocess.
    Requires the Claude CLI to be installed and authenticated.

    Features:
    - No API key management needed (uses CLI auth)
    - Supports all language pairs
    - Batch title+description in single call via translate_item() override
    - Fallback to two separate calls if JSON parsing fails
    """

    def __init__(self):
        """Initialize Claude CLI engine."""
        logger.info("Initialized Claude CLI engine")

    @property
    def name(self) -> str:
        return "claude"

    def supports_locale_pair(self, source: str, target: str) -> bool:
        """Claude supports all language pairs."""
        return True

    async def translate(
        self,
        text: str,
        source_locale: str,
        target_locale: str,
        translation_type: str = 'display',
        context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Translate a single text string using `claude -p`.

        Args:
            text: Text to translate
            source_locale: Source locale code (e.g., "ja-JP")
            target_locale: Target locale code (e.g., "en-US")
            translation_type: 'canonical' or 'display' (affects prompt)
            context: Additional context (ignored for now)

        Returns:
            Translated text

        Raises:
            TranslationError: If translation fails
            AuthenticationError: If CLI not found or not authenticated
            RateLimitError: If rate limited
        """
        if not text or not text.strip():
            return text

        if translation_type == 'canonical':
            prompt = (
                f"Translate the following text from {source_locale} to normalized English (en-US). "
                f"Produce a stable, literal, machine-consistent English meaning. "
                f"Return ONLY the translated text, no explanations.\n\n{text}"
            )
        else:
            prompt = (
                f"Translate the following text from {source_locale} to {target_locale}. "
                f"Translate naturally while preserving tone and cultural nuances. "
                f"Return ONLY the translated text, no explanations.\n\n{text}"
            )

        return await self._run_claude_cli(prompt)

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
        Translate title + description in a single `claude -p` call.

        Attempts batch JSON mode first; falls back to two separate calls
        if JSON parsing fails.
        """
        if not description:
            # Only title — use base class (single translate() call)
            return await super().translate_item(
                title, description, source_locale, target_locale,
                translation_type, context
            )

        # Batch mode: translate both in one call
        try:
            result = await self._translate_batch(
                title, description, source_locale, target_locale, translation_type
            )
            if result is not None:
                return result
        except Exception as e:
            logger.debug(f"Claude batch translation failed, falling back to separate calls: {e}")

        # Fallback: two separate translate() calls via base class
        return await super().translate_item(
            title, description, source_locale, target_locale,
            translation_type, context
        )

    async def _translate_batch(
        self,
        title: str,
        description: str,
        source_locale: str,
        target_locale: str,
        translation_type: str
    ) -> Optional[TranslationResult]:
        """
        Attempt batch translation of title + description via single CLI call.

        Returns TranslationResult on success, None if JSON parsing fails.
        Raises translation exceptions (AuthenticationError, etc.) on CLI errors.
        """
        if translation_type == 'canonical':
            prompt = (
                f"Translate the following from {source_locale} to normalized English (en-US). "
                f"Produce stable, literal, machine-consistent English meaning. "
                f"Return JSON only, no markdown fences, no explanations:\n"
                f'{{"title": "translated title here", "description": "translated description here"}}\n\n'
                f"Title: {title}\n"
                f"Description: {description}"
            )
        else:
            prompt = (
                f"Translate the following from {source_locale} to {target_locale}. "
                f"Translate naturally while preserving tone and cultural nuances. "
                f"Return JSON only, no markdown fences, no explanations:\n"
                f'{{"title": "translated title here", "description": "translated description here"}}\n\n'
                f"Title: {title}\n"
                f"Description: {description}"
            )

        raw = await self._run_claude_cli(prompt)

        # Strip markdown code fences if present
        stripped = re.sub(r'^```(?:json)?\s*\n?', '', raw, flags=re.MULTILINE)
        stripped = re.sub(r'\n?```\s*$', '', stripped, flags=re.MULTILINE)
        stripped = stripped.strip()

        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            logger.debug(f"Claude batch JSON parse failed, raw output: {raw[:200]}")
            return None

        if not isinstance(parsed, dict) or 'title' not in parsed:
            logger.debug(f"Claude batch JSON missing 'title' key: {parsed}")
            return None

        return TranslationResult(
            title=parsed['title'],
            description=parsed.get('description'),
            engine=self.name,
            source_locale=source_locale,
            target_locale=target_locale,
            metadata={'mode': 'batch'}
        )

    async def _run_claude_cli(self, prompt: str) -> str:
        """
        Run `claude -p <prompt>` and return stdout.

        Raises appropriate exception subclasses for health tracking.
        """
        try:
            process = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    'claude', '-p', prompt,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                ),
                timeout=CLAUDE_CLI_TIMEOUT
            )
        except FileNotFoundError:
            raise AuthenticationError(
                "Claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code",
                engine=self.name
            )
        except asyncio.TimeoutError:
            raise TranslationError(
                f"Claude CLI process creation timed out after {CLAUDE_CLI_TIMEOUT}s",
                engine=self.name
            )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=CLAUDE_CLI_TIMEOUT
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise TranslationError(
                f"Claude CLI timed out after {CLAUDE_CLI_TIMEOUT}s",
                engine=self.name
            )

        if process.returncode == 0:
            return stdout.decode().strip()

        # Parse stderr for specific error types
        error_msg = stderr.decode().strip()
        if not error_msg:
            error_msg = stdout.decode().strip() or f"Claude CLI exited with code {process.returncode}"

        error_lower = error_msg.lower()

        if 'not authenticated' in error_lower or 'authentication' in error_lower:
            raise AuthenticationError(error_msg, engine=self.name)
        elif 'rate limit' in error_lower or 'too many requests' in error_lower:
            raise RateLimitError(error_msg, engine=self.name)
        else:
            raise TranslationError(error_msg, engine=self.name)

    async def close(self):
        """No resources to clean up."""
        pass
