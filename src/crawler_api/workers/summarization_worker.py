"""
Summarization worker - AI-powered summaries via claude -p.

Replaces raw truncated description_original with a Claude-generated summary
that captures what the article/post actually says.

Surface categories and prompt selection:
- news_article : RSS feeds + Wenxuecity          → 2-3 sentence factual summary
- discussion    : Reddit self-posts + HN text     → 2 sentence post summary
- youtube       : YouTube (text + engagement)     → 2 sentence summary + signals line
- signals_only  : Link posts / Google Trends      → engagement signals formatted line
- skip          : Twitter (already full text), stubs, no content + no signals

Architecture mirrors translation_worker.py:
- Async poll loop (never blocks collection)
- summary_status field: pending → complete/failed/skipped
- Writes result back to description_original (repurposed field)
- Raw content always preserved in raw_payload
"""

import asyncio
import gc
import json
import logging
import os
import re
import sys
import time
from typing import Optional

import django
from asgiref.sync import sync_to_async
from django.db.models import Q


sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

from django.db import close_old_connections, reset_queries
from crawler_admin.models import TrendItem

logger = logging.getLogger(__name__)

POLL_INTERVAL = int(os.getenv('SUMMARIZATION_WORKER_POLL_INTERVAL', '60'))
CLAUDE_TIMEOUT_SINGLE = 90    # seconds — per-item fallback calls
CLAUDE_TIMEOUT_BATCH  = 240   # seconds — 10-item batch call
BATCH_SIZE = 10
CONTENT_MAX_CHARS  = 800      # truncate article content before sending to Claude
COMMENTS_MAX_CHARS = 400      # truncate comments before sending to Claude
FAILED_RETRY_INTERVAL = 3600  # 1 hour

_last_failed_retry_time: float = 0

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

# Compressed single-item prompts — used only in per-item fallback calls.
# The primary path is the 10-item batch prompt built by _build_batch_prompt().

PROMPT_NEWS_ARTICLE = (
    "Platform: {platform} | Language: {source_locale}\n"
    "Title: {title}\n"
    "Content: {content}\n\n"
    "2-3 sentences in {source_locale}: what happened, who, why it matters. No preamble."
)

PROMPT_DISCUSSION = (
    "Platform: {platform} | Language: {source_locale}\n"
    "Title: {title}\n"
    "Content: {content}\n\n"
    "2 sentences in {source_locale}: main point or question raised."
)

PROMPT_YOUTUBE = (
    "Language: {source_locale}\n"
    "Title: {title}\n"
    "Description: {content}\n"
    "Comments: {top_comments}\n\n"
    "1-2 sentences in {source_locale}: what this video is about."
)

PROMPT_SIGNALS_ONLY = (
    "Platform: {platform} | Language: {source_locale}\n"
    "Title: {title}\n"
    "Comments: {top_comments}\n\n"
    "1-2 sentences in {source_locale}: what this is about and key community reaction. No invented facts."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_engagement(signals: dict) -> str:
    """Convert engagement_signals dict to a human-readable string."""
    if not signals:
        return ""
    parts = []
    if signals.get('views'):
        v = signals['views']
        parts.append(f"🎬 {_fmt_num(v)} views")
    if signals.get('upvotes'):
        parts.append(f"{_fmt_num(signals['upvotes'])} 👍")
    if signals.get('score') and 'upvotes' not in signals:
        parts.append(f"🔥 {_fmt_num(signals['score'])} pts")
    if signals.get('comments'):
        parts.append(f"{_fmt_num(signals['comments'])} 💬")
    if signals.get('shares'):
        parts.append(f"{_fmt_num(signals['shares'])} 🔁")
    if signals.get('traffic'):
        parts.append(f"📈 {signals['traffic']} searches")
    return " · ".join(parts) if parts else str(signals)


def _fmt_num(n) -> str:
    """Format large numbers as 1.2K / 4.5M."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def _get_surface_platform(item: TrendItem) -> str:
    """Safely retrieve the surface platform string."""
    try:
        return item.surface.platform if item.surface else ''
    except Exception:
        return ''


def _classify(item: TrendItem) -> str:
    """
    Determine which summarization strategy to apply.

    Returns one of: 'news_article', 'discussion', 'youtube',
                    'signals_only', 'skip'
    """
    platform = _get_surface_platform(item).lower()
    desc = (item.description_original or '').strip()
    has_text = len(desc) > 30
    has_signals = bool(item.engagement_signals)

    # Always skip Twitter (tweet text is already the full content)
    # Always skip stub surfaces
    if 'twitter' in platform or 'yahoo_jp' in platform:
        return 'skip'

    if 'youtube' in platform:
        # YouTube always gets summary + engagement line
        return 'youtube'

    if has_text:
        if 'reddit' in platform or 'hackernews' in platform or 'hacker' in platform:
            return 'discussion'
        return 'news_article'

    # No meaningful text — fall back to signals if available
    if has_signals:
        return 'signals_only'

    return 'skip'


def _format_top_comments(raw_payload: dict) -> str:
    """Format top_comments list from raw_payload into a numbered string for the prompt."""
    comments = raw_payload.get('top_comments', [])
    if not comments:
        return "(no comments available)"
    return "\n".join(f"{i+1}. {c}" for i, c in enumerate(comments))


def _build_prompt(item: TrendItem, strategy: str) -> str:
    """Build a single-item Claude prompt (used for per-item fallback calls)."""
    platform = _get_surface_platform(item)
    source_locale = item.original_locale or 'en-US'
    title = item.title_original or ''
    content = (item.description_original or '').strip()[:CONTENT_MAX_CHARS]
    top_comments = _format_top_comments(item.raw_payload or {})[:COMMENTS_MAX_CHARS]

    if strategy == 'news_article':
        return PROMPT_NEWS_ARTICLE.format(
            platform=platform, source_locale=source_locale, title=title, content=content
        )
    if strategy == 'discussion':
        return PROMPT_DISCUSSION.format(
            platform=platform, source_locale=source_locale, title=title, content=content
        )
    if strategy == 'youtube':
        return PROMPT_YOUTUBE.format(
            source_locale=source_locale, title=title, content=content,
            top_comments=top_comments
        )
    if strategy == 'signals_only':
        return PROMPT_SIGNALS_ONLY.format(
            platform=platform, source_locale=source_locale, title=title,
            top_comments=top_comments
        )
    raise ValueError(f"Unknown strategy: {strategy}")


def _build_batch_prompt(items_with_strategies: list) -> str:
    """
    Build a single Claude prompt for up to 10 items.

    Each item gets inline per-item instructions based on its strategy.
    Expected response format:
        [1] <summary>
        [2] <summary>
        ...
    """
    n = len(items_with_strategies)
    format_lines = "\n".join(f"[{i}] <summary>" for i in range(1, n + 1))

    lines = [
        "Summarize each item per its instruction. Reply with exactly this format — nothing else:",
        format_lines,
        "",
        "---",
    ]

    for idx, (item, strategy) in enumerate(items_with_strategies, 1):
        platform = _get_surface_platform(item)
        source_locale = item.original_locale or 'en-US'
        title = item.title_original or ''
        content = (item.description_original or '').strip()[:CONTENT_MAX_CHARS]
        top_comments = _format_top_comments(item.raw_payload or {})[:COMMENTS_MAX_CHARS]

        lines.append(f"[{idx}] {strategy} | {platform} | {source_locale}")
        lines.append(f"Title: {title}")

        if strategy == 'news_article':
            lines.append(f"Content: {content}")
            lines.append(f"→ 2-3 sentences in {source_locale}. What happened, who, why it matters. No preamble.")
        elif strategy == 'discussion':
            lines.append(f"Content: {content}")
            lines.append(f"→ 2 sentences in {source_locale}. Main point or question raised.")
        elif strategy == 'youtube':
            lines.append(f"Description: {content}")
            if top_comments:
                lines.append(f"Comments: {top_comments}")
            lines.append(f"→ 1-2 sentences in {source_locale}. What this video is about.")
        elif strategy == 'signals_only':
            if top_comments:
                lines.append(f"Comments: {top_comments}")
            lines.append(f"→ 1-2 sentences in {source_locale}. What this is about and key community reaction. No invented facts.")

        lines.append("")

    return "\n".join(lines)


def _parse_batch_response(raw: str, count: int) -> dict:
    """
    Parse a numbered batch response into {1: summary, 2: summary, ...}.

    Handles format:
        [1] Summary text here...
        [2] Another summary...
    """
    result = {}
    pattern = re.compile(r'^\[(\d+)\]\s*(.+?)(?=\n\[\d+\]|\Z)', re.MULTILINE | re.DOTALL)
    for match in pattern.finditer(raw):
        idx = int(match.group(1))
        summary = match.group(2).strip()
        if 1 <= idx <= count and summary:
            result[idx] = summary
    return result


async def _run_claude(prompt: str, timeout: int = CLAUDE_TIMEOUT_SINGLE) -> str:
    """
    Call `claude -p <prompt>` and return stdout.
    Raises RuntimeError on failure.

    Use timeout=CLAUDE_TIMEOUT_BATCH for multi-item batch calls.
    """
    # 2026-04-14: strip Claude Code harness overhead on every subprocess.
    # See topic_llm_classifier.py (commit 7163c925) for full rationale —
    # each plain `claude -p` call was burning ~15-25 KB of input tokens on
    # the default system prompt, skill manifests, tool catalogue, and
    # CLAUDE.md that summarization never uses. Flags strip the harness
    # down to just the model + our prompt. NOTE: prompt is passed as a
    # positional arg (not stdin) in this async path, so it MUST come
    # LAST, after all flags.
    try:
        process = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                'claude', '-p',
                '--model', 'sonnet',
                '--system-prompt',
                'You summarize article descriptions. Output ONLY the '
                'indexed [N] summary lines requested in the user message. '
                'No preamble, no markdown, no explanation.',
                '--disable-slash-commands',
                '--tools', '',
                '--setting-sources', 'user',
                '--no-session-persistence',
                '--output-format', 'text',
                prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            ),
            timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError("Claude CLI not found. Install: npm install -g @anthropic-ai/claude-code")
    except asyncio.TimeoutError:
        raise RuntimeError(f"Claude CLI process creation timed out after {timeout}s")

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError(f"Claude CLI timed out after {timeout}s")

    if process.returncode == 0:
        return stdout.decode().strip()

    err = stderr.decode().strip() or stdout.decode().strip() or f"exit code {process.returncode}"
    raise RuntimeError(f"Claude CLI error: {err}")


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

async def process_batch(batch_size: int = BATCH_SIZE) -> int:
    """
    Pick up to batch_size items to summarize, write back to description_original.

    Only processes items with summary_status='queued' — pre-selected by the
    translation worker as the top X% per locale (including zh).

    - zh items: queued at top 2% by hotness within zh; summarized but not translated
    - non-zh items: queued at top X% by hotness within their locale; summarized then translated

    Items still 'pending' are intentionally ignored — they did not make the
    hotness cut and will never be displayed, so summarizing them would waste LLM calls.

    Items are ordered by hotness DESC so the most important items are summarized first.
    All non-skip items are summarized in a single batch claude -p call.
    Falls back to individual calls per item if the batch response cannot be parsed.

    Returns count of processed items.
    """
    items = await sync_to_async(list)(
        TrendItem.objects.filter(
            summary_status='queued'  # Pre-selected top X% per locale (includes zh)
        )
        .select_related('surface')
        .order_by('-hotness', '-collected_at')[:batch_size]
    )

    if not items:
        logger.debug("No items ready for summarization")
        return 0

    logger.info(f"Summarizing {len(items)} item(s)")

    # --- Classify all items ---
    classified = [(item, _classify(item)) for item in items]

    # --- Handle skips immediately ---
    to_summarize = []
    for item, strategy in classified:
        if strategy == 'skip':
            item.summary_status = 'skipped'
            await sync_to_async(item.save)(update_fields=['summary_status'])
            logger.debug(f"⏭️  Skipped item #{item.id} (platform={_get_surface_platform(item)})")
        else:
            to_summarize.append((item, strategy))

    if not to_summarize:
        return len(classified)

    # --- Attempt single batch call for all non-skip items ---
    summaries: dict = {}  # {1-based index → summary text}
    try:
        prompt = _build_batch_prompt(to_summarize)
        raw = await _run_claude(prompt, timeout=CLAUDE_TIMEOUT_BATCH)
        summaries = _parse_batch_response(raw, len(to_summarize))

        if len(summaries) < len(to_summarize):
            logger.warning(
                f"Batch response incomplete: got {len(summaries)}/{len(to_summarize)} summaries — "
                f"will fall back to individual calls for missing items"
            )
    except Exception as e:
        logger.warning(f"Batch summarization failed ({e}) — falling back to individual calls")

    # --- Apply results; fall back per item if not in batch response ---
    for idx, (item, strategy) in enumerate(to_summarize, 1):
        if idx in summaries:
            summary = summaries[idx]

            # Append Python-formatted engagement line for youtube / signals_only
            if strategy in ('youtube', 'signals_only'):
                eng_line = _format_engagement(item.engagement_signals or {})
                if eng_line:
                    summary = f"{summary}\n{eng_line}"

            item.description_original = summary
            item.summary_status = 'complete'
            await sync_to_async(item.save)(
                update_fields=['description_original', 'summary_status']
            )
            logger.info(
                f"✅ Summarized item #{item.id} | strategy={strategy} | "
                f"platform={_get_surface_platform(item)} | chars={len(summary)}"
            )
        else:
            # Fallback: individual call for this item
            try:
                prompt = _build_prompt(item, strategy)
                summary = await _run_claude(prompt, timeout=CLAUDE_TIMEOUT_SINGLE)

                if strategy in ('youtube', 'signals_only'):
                    eng_line = _format_engagement(item.engagement_signals or {})
                    if eng_line:
                        summary = f"{summary}\n{eng_line}"

                item.description_original = summary
                item.summary_status = 'complete'
                await sync_to_async(item.save)(
                    update_fields=['description_original', 'summary_status']
                )
                logger.info(
                    f"✅ Summarized (fallback) item #{item.id} | strategy={strategy} | "
                    f"platform={_get_surface_platform(item)} | chars={len(summary)}"
                )
            except Exception as e:
                item.summary_status = 'failed'
                await sync_to_async(item.save)(update_fields=['summary_status'])
                logger.warning(f"❌ Summarization failed item #{item.id}: {e}")

    return len(classified)


async def retry_failed(batch_size: int = 50) -> int:
    """
    Reset failed items back to the correct pending state every FAILED_RETRY_INTERVAL.

    - zh items: reset to 'pending'  (summarization worker picks them up directly)
    - non-zh items: reset to 'queued' (they were pre-selected before failing;
      reset to queued so the summarization worker retries them without waiting
      for the next pre-selection pass)
    """
    global _last_failed_retry_time
    now = time.time()
    if now - _last_failed_retry_time < FAILED_RETRY_INTERVAL:
        return 0
    _last_failed_retry_time = now

    zh_count = await sync_to_async(
        TrendItem.objects.filter(summary_status='failed', lang_group='zh').update
    )(summary_status='pending')

    non_zh_count = await sync_to_async(
        TrendItem.objects.filter(summary_status='failed')
        .exclude(lang_group='zh')
        .update
    )(summary_status='queued')

    total = zh_count + non_zh_count
    if total:
        logger.info(
            f"♻️  Reset {total} failed summarization(s): "
            f"{zh_count} zh→pending, {non_zh_count} non-zh→queued"
        )
    return total


async def run_worker_loop():
    logger.info("Summarization worker started")
    logger.info(f"POLL_INTERVAL={POLL_INTERVAL}s  BATCH_SIZE={BATCH_SIZE}  CLAUDE_TIMEOUT_BATCH={CLAUDE_TIMEOUT_BATCH}s")

    while True:
        try:
            await retry_failed()
            count = await process_batch()
            if count:
                logger.info(f"Cycle complete: summarized {count} item(s)")
        except Exception as e:
            logger.error(f"Worker loop error: {e}", exc_info=True)
        finally:
            # Release Django ORM objects and DB connection state — same fix as
            # region/topic classifier workers (2026-04-17 OOM kill).
            await sync_to_async(close_old_connections)()
            await sync_to_async(reset_queries)()
            gc.collect()

        await asyncio.sleep(POLL_INTERVAL)


def main():
    logging.basicConfig(
        level=os.getenv('LOG_LEVEL', 'INFO'),
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )
    try:
        asyncio.run(run_worker_loop())
    except KeyboardInterrupt:
        logger.info("Summarization worker stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
