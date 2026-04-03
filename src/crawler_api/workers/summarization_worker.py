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
import json
import logging
import os
import sys
import time
from typing import Optional

import django
from asgiref.sync import sync_to_async
from django.db.models import Q

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

from crawler_admin.models import TrendItem

logger = logging.getLogger(__name__)

POLL_INTERVAL = int(os.getenv('SUMMARIZATION_WORKER_POLL_INTERVAL', '60'))
CLAUDE_TIMEOUT = 90  # seconds per call — summaries take longer than translations
BATCH_SIZE = 5
FAILED_RETRY_INTERVAL = 3600  # 1 hour

_last_failed_retry_time: float = 0

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

PROMPT_NEWS_ARTICLE = """You are summarizing a news article for a trending news feed.

Platform: {platform}
Language: {source_locale}
Title: {title}
Content: {content}

Write a 2-3 sentence summary in the same language as the original content ({source_locale}) that captures what actually happened, who is involved, and why it matters. Be factual and direct. Do not start with "This article" or "The article". Output only the summary, nothing else."""

PROMPT_DISCUSSION = """You are summarizing a social post for a trending feed.

Platform: {platform}
Language: {source_locale}
Title: {title}
Content: {content}

Write 2 sentences in the same language as the original content ({source_locale}) capturing the main point or question being raised. Be direct and concrete. Output only the summary, nothing else."""

PROMPT_YOUTUBE = """You are summarizing a YouTube video for a trending feed.

Language: {source_locale}
Title: {title}
Description: {content}
Top viewer comments:
{top_comments}

Write 1-2 sentences in the same language as the original content ({source_locale}) summarizing what this video is about and what viewers are saying. Output only the summary, nothing else."""

PROMPT_SIGNALS_ONLY = """You are writing a short description for a trending item that has no article body.

Platform: {platform}
Language: {source_locale}
Title: {title}
Top comments from the community:
{top_comments}

Write 1-2 sentences in the same language as the original content ({source_locale}) capturing what this is about and the key reaction from the community (based on comments above — do not invent facts). Output only the summary, nothing else."""


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
    """Build the Claude prompt for a given item and strategy."""
    platform = _get_surface_platform(item)
    source_locale = item.original_locale or 'en-US'
    title = item.title_original or ''
    content = (item.description_original or '').strip()
    engagement = _format_engagement(item.engagement_signals)
    top_comments = _format_top_comments(item.raw_payload or {})

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


async def _run_claude(prompt: str) -> str:
    """
    Call `claude -p <prompt>` and return stdout.
    Raises RuntimeError on failure.
    """
    try:
        process = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                'claude', '-p', prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            ),
            timeout=CLAUDE_TIMEOUT,
        )
    except FileNotFoundError:
        raise RuntimeError("Claude CLI not found. Install: npm install -g @anthropic-ai/claude-code")
    except asyncio.TimeoutError:
        raise RuntimeError(f"Claude CLI process creation timed out after {CLAUDE_TIMEOUT}s")

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=CLAUDE_TIMEOUT
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError(f"Claude CLI timed out after {CLAUDE_TIMEOUT}s")

    if process.returncode == 0:
        return stdout.decode().strip()

    err = stderr.decode().strip() or stdout.decode().strip() or f"exit code {process.returncode}"
    raise RuntimeError(f"Claude CLI error: {err}")


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

async def process_batch(batch_size: int = BATCH_SIZE) -> int:
    """
    Pick up to batch_size pending items, summarize, write back to description_original.
    Returns count of processed items.
    """
    items = await sync_to_async(list)(
        TrendItem.objects.filter(summary_status='pending')
        .select_related('surface')
        .order_by('-collected_at')[:batch_size]
    )

    if not items:
        logger.debug("No items pending summarization")
        return 0

    logger.info(f"Summarizing {len(items)} item(s)")
    processed = 0

    for item in items:
        strategy = _classify(item)

        # --- Skip immediately ---
        if strategy == 'skip':
            item.summary_status = 'skipped'
            await sync_to_async(item.save)(update_fields=['summary_status'])
            logger.debug(f"⏭️  Skipped item #{item.id} (platform={_get_surface_platform(item)})")
            processed += 1
            continue

        # --- Summarize ---
        try:
            prompt = _build_prompt(item, strategy)
            summary = await _run_claude(prompt)

            # For youtube and signals_only: append Python-formatted engagement
            # line as Line 2. This replaces the LLM doing the formatting.
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
                f"platform={_get_surface_platform(item)} | "
                f"chars={len(summary)}"
            )

        except Exception as e:
            item.summary_status = 'failed'
            await sync_to_async(item.save)(update_fields=['summary_status'])
            logger.warning(f"❌ Summarization failed item #{item.id}: {e}")

        processed += 1

    return processed


async def retry_failed(batch_size: int = 50) -> int:
    """Reset failed items to pending every FAILED_RETRY_INTERVAL seconds."""
    global _last_failed_retry_time
    now = time.time()
    if now - _last_failed_retry_time < FAILED_RETRY_INTERVAL:
        return 0
    _last_failed_retry_time = now

    failed = await sync_to_async(list)(
        TrendItem.objects.filter(summary_status='failed')[:batch_size]
    )
    if not failed:
        return 0

    count = 0
    for item in failed:
        item.summary_status = 'pending'
        await sync_to_async(item.save)(update_fields=['summary_status'])
        count += 1

    if count:
        logger.info(f"♻️  Reset {count} failed summarization(s) to pending")
    return count


async def run_worker_loop():
    logger.info("Summarization worker started")
    logger.info(f"POLL_INTERVAL={POLL_INTERVAL}s  BATCH_SIZE={BATCH_SIZE}  CLAUDE_TIMEOUT={CLAUDE_TIMEOUT}s")

    while True:
        try:
            await retry_failed()
            count = await process_batch()
            if count:
                logger.info(f"Cycle complete: summarized {count} item(s)")
        except Exception as e:
            logger.error(f"Worker loop error: {e}", exc_info=True)

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
