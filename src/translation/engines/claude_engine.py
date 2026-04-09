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
CLAUDE_CLI_TIMEOUT = 60        # single-item calls
CLAUDE_CLI_TIMEOUT_BATCH = 120 # multi-item batch calls (5 items)


def _render_prompt(template: str, **kwargs) -> str:
    """
    Substitute {variable} placeholders in a prompt template.

    Uses simple string replacement (not Python .format()) so that
    literal curly braces in JSON examples are left untouched as long
    as they do not match any variable name.
    """
    result = template
    for key, value in kwargs.items():
        result = result.replace(f'{{{key}}}', str(value) if value is not None else '')
    return result


def _get_config_sync():
    """Synchronous DB fetch of TranslationConfig singleton."""
    from translation.models import TranslationConfig
    return TranslationConfig.get_config()


async def _get_config():
    """Async-safe wrapper: fetch TranslationConfig from an async context."""
    from asgiref.sync import sync_to_async
    return await sync_to_async(_get_config_sync)()


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

        config = await _get_config()
        platform = (context or {}).get('source_platform', 'unknown')

        if translation_type == 'canonical':
            template = config.canonical_prompt
        else:
            template = config.display_prompt

        prompt = _render_prompt(
            template,
            source_locale=source_locale,
            target_locale=target_locale,
            platform=platform,
            text=text,
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
                title, description, source_locale, target_locale, translation_type, context
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
        translation_type: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Optional[TranslationResult]:
        """
        Attempt batch translation of title + description via single CLI call.

        Returns TranslationResult on success, None if JSON parsing fails.
        Raises translation exceptions (AuthenticationError, etc.) on CLI errors.
        """
        config = await _get_config()
        platform = (context or {}).get('source_platform', 'unknown')

        if translation_type == 'canonical':
            template = config.canonical_batch_prompt
        else:
            template = config.display_batch_prompt

        prompt = _render_prompt(
            template,
            source_locale=source_locale,
            target_locale=target_locale,
            platform=platform,
            title=title,
            description=description,
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

    async def translate_items_batch(
        self,
        items: list,
        target_locale: str,
        translation_type: str = 'display',
    ) -> list:
        """
        Translate up to 5 items in a single claude -p call.

        Args:
            items: List of dicts with keys: title, description, source_locale, platform
            target_locale: Target locale code (e.g., 'en-US', 'zh-Hans')
            translation_type: 'canonical' or 'display'

        Returns:
            List of TranslationResult (or None for items that could not be parsed).
            Raises translation exceptions on CLI-level failures.
        """
        if not items:
            return []

        # Build per-item block
        item_lines = []
        for i, item in enumerate(items, 1):
            item_lines.append(
                f"[{i}] From: {item.get('source_locale', 'unknown')} | "
                f"Platform: {item.get('platform', 'unknown')}"
            )
            item_lines.append(f"Title: {item.get('title', '')}")
            item_lines.append(f"Description: {(item.get('description') or '').strip()}")
            item_lines.append("")

        items_text = "\n".join(item_lines)

        if translation_type == 'canonical':
            prompt = (
                "Translate each item to en-US. Literal, machine-consistent.\n"
                "Return JSON array in same order — no other text:\n"
                '[{"title":"...","description":"..."}, ...]\n\n'
                + items_text
            )
        else:
            prompt = (
                f"Translate each item to {target_locale}. Natural, preserve tone. "
                "Keep acronyms (e.g. ACAB) in their original form — do not translate them.\n"
                "Return JSON array in same order — no other text:\n"
                '[{"title":"...","description":"..."}, ...]\n\n'
                + items_text
            )

        raw = await self._run_claude_cli(prompt, timeout=CLAUDE_CLI_TIMEOUT_BATCH)

        # Strip markdown code fences if present
        stripped = re.sub(r'^```(?:json)?\s*\n?', '', raw, flags=re.MULTILINE)
        stripped = re.sub(r'\n?```\s*$', '', stripped, flags=re.MULTILINE)
        stripped = stripped.strip()

        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            logger.debug(f"Multi-batch JSON parse failed: {raw[:300]}")
            return [None] * len(items)

        if not isinstance(parsed, list):
            logger.debug(f"Multi-batch response is not a list: {type(parsed)}")
            return [None] * len(items)

        results = []
        for i, item in enumerate(items):
            entry = parsed[i] if i < len(parsed) else None
            if isinstance(entry, dict) and 'title' in entry:
                results.append(TranslationResult(
                    title=entry['title'],
                    description=entry.get('description'),
                    engine=self.name,
                    source_locale=item.get('source_locale', ''),
                    target_locale=target_locale,
                    metadata={'mode': 'multi_batch'}
                ))
            else:
                results.append(None)

        return results

    async def _run_claude_cli(self, prompt: str, timeout: int = CLAUDE_CLI_TIMEOUT) -> str:
        """
        Run `claude -p <prompt>` and return stdout.

        Raises appropriate exception subclasses for health tracking.
        Use timeout=CLAUDE_CLI_TIMEOUT_BATCH for multi-item batch calls.
        """
        try:
            process = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    'claude', '-p', '--model', 'opus', prompt,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                ),
                timeout=timeout
            )
        except FileNotFoundError:
            raise AuthenticationError(
                "Claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code",
                engine=self.name
            )
        except asyncio.TimeoutError:
            raise TranslationError(
                f"Claude CLI process creation timed out after {timeout}s",
                engine=self.name
            )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise TranslationError(
                f"Claude CLI timed out after {timeout}s",
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
