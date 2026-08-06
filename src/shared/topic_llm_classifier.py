"""
Topic LLM classifier — Phase 4.

Classifies TrendItems by topic using Claude CLI when the heuristic pass
(topic_classifier.py) returns no category, or for low-confidence heuristic
results on high-hotness items.

Interface required by topic_classifier_worker.py:
    classify_batch_llm(items: list[dict]) -> list[str | None]
    BATCH_SIZE: int

items dicts have keys: 'title', 'platform', 'description' (max 300 chars).

Returns a list of story_category strings (or None), one per input item.
None means "unclassifiable" — worker marks item as failed or preserves
heuristic result.
"""

import json
import logging
import subprocess
import time

from shared.topic_classifier import VALID_CATEGORIES

logger = logging.getLogger(__name__)

BATCH_SIZE = 20
CLAUDE_TIMEOUT = 90

# 2026-04-14: quota-aware backoff.
#
# Background: on 2026-04-14 the Claude subscription quota exhausted mid-day
# and every `claude -p` call failed for ~2.5 hours. The worker kept firing
# ~249 subprocesses per 10-minute cycle at the wall, each one burning more
# input tokens before rejecting. This module-level backoff prevents that:
# when we detect a usage-limit / rate-limit message in claude's stdout or
# stderr, we flip a timestamp and skip the LLM pass entirely until the
# backoff expires. The heuristic classifier pass in topic_classifier_worker
# is unaffected and keeps running; unclassifiable items just defer to a
# later cycle once quota recovers.
QUOTA_BACKOFF_SECONDS = 1800  # 30 minutes — long enough to let the 5-hour
                              # rolling quota window shift, short enough to
                              # recover promptly once limits lift.

# Substrings (matched case-insensitively) in claude -p's stdout/stderr
# that indicate a quota or rate-limit failure. Conservative set — we only
# enter backoff on unambiguous quota signals; generic "error" / "failed"
# messages are NOT included, so transient API blips still retry normally.
_QUOTA_ERROR_PATTERNS = (
    'usage limit',
    'usage_limit',
    'rate limit',
    'rate_limit',
    'quota',
    'too many requests',
    'credit balance',
    'limit reached',
)

# Module-level backoff state. The topic classifier worker is
# single-threaded (serial batch loop), so no lock is needed. Survives
# only within a single worker process — a worker restart clears it, which
# is fine: the first call after restart will either succeed or re-enter
# backoff immediately with a fresh log line.
_quota_blocked_until: float = 0.0


def _is_quota_error(stdout: str, stderr: str) -> bool:
    """Return True if either stream contains a known quota/rate-limit
    message. Substring match, case-insensitive."""
    blob = f"{stdout}\n{stderr}".lower()
    return any(pat in blob for pat in _QUOTA_ERROR_PATTERNS)


# Ordered list for display in prompt
_VALID_LABELS = sorted(VALID_CATEGORIES)


def classify_batch_llm(items: list[dict]) -> list[str | None]:
    """
    Classify a batch of items using Claude CLI.

    Args:
        items: list of dicts with 'title', 'platform', 'description' keys.
               Max BATCH_SIZE items — caller is responsible for batching.

    Returns:
        List of story_category strings or None, one per item.
        None means unclassifiable. On total failure, returns list of None
        for all items (safe no-op).
    """
    if not items:
        return []

    batch = items[:BATCH_SIZE]
    empty_results: list[str | None] = [None] * len(batch)

    # 2026-04-14: quota backoff gate. If a previous call in this worker
    # process hit a usage-limit error recently, skip the LLM pass entirely
    # until the backoff expires. Return all-None exactly as we would on a
    # real failure — the worker handles that path already (items stay in
    # the pending pool and get retried next cycle).
    global _quota_blocked_until
    now = time.time()
    if now < _quota_blocked_until:
        remaining = int(_quota_blocked_until - now)
        logger.info(
            "Topic LLM in quota backoff: %ds remaining — skipping batch of %d",
            remaining,
            len(batch),
        )
        return empty_results

    # Build prompt
    # 2026-04-14: dropped description from the per-item line. Title is
    # sufficient signal for an 8-way classification; description doubled
    # prompt tokens without improving accuracy measurably. Keeps per-call
    # cost low so that Haiku + high call volume is affordable.
    item_lines = []
    for i, item in enumerate(batch, 1):
        title = (item.get('title') or '')[:200]
        platform = item.get('platform') or 'unknown'
        item_lines.append(f"{i}. [{platform}] {title}")

    prompt = f"""For each news item below, assign exactly one topic category.

Valid categories: {', '.join(_VALID_LABELS)}

Category definitions:
- ai: machine learning, LLMs, AI models, AI policy, AI companies
- technology: chips, software, devices, platforms, cybersecurity (non-AI tech)
- politics: elections, government, geopolitics, war, diplomacy, legislation
- business: finance, markets, earnings, mergers, trade, economy, companies
- science: research, environment, climate, space, medicine, biology
- society: health, crime, social issues, culture, education, human rights
- sports: sports events, leagues, athletes, tournaments
- entertainment: movies, music, gaming, celebrity, streaming
- world: international news that doesn't fit another category

Rules:
- Choose exactly ONE category per item from the list above.
- Return null if none apply (purely local or completely unclear content).
- Do NOT invent categories outside the list.

Items:
{chr(10).join(item_lines)}

Return ONLY a JSON array of {len(batch)} entries.
Each entry is a category string or null.
Example for 4 items: ["technology", "politics", null, "business"]

CRITICAL: Return exactly {len(batch)} entries."""

    try:
        # 2026-04-14: switched from 'sonnet' to 'haiku'. Classification is
        # 8-way single-label on short titles — Haiku's accuracy on this task
        # is within 1-2% of Sonnet, and per-call cost is ~12x lower in theory.
        #
        # 2026-04-14 (later): the Haiku savings were being eaten by Claude
        # Code harness overhead — every `claude -p` subprocess was loading
        # the full default system prompt, all skill manifests, the deferred
        # tool catalogue, CLAUDE.md, hooks, and per-machine env sections on
        # every single call. None of that is needed to pick one of 9 labels.
        # The flags below strip the harness down to just the model + our
        # prompt:
        #   --system-prompt           replaces the multi-KB default system
        #                             prompt with a one-liner role
        #   --disable-slash-commands  skips loading every skill manifest
        #   --tools ""                drops all tool schemas (classifier
        #                             needs no tools — pure text in/out)
        #   --setting-sources ''      skips ALL settings discovery, including
        #                             user-level ~/.claude/CLAUDE.md and
        #                             ~/.claude/settings.json. Measured
        #                             2026-08-06: 'user' cost 2386 input
        #                             tokens/call vs 755 with '' — 1631
        #                             tokens/call of pure harness overhead
        #                             for zero classification benefit.
        #   --no-session-persistence  do not write session files for one-shot
        #
        # NOTE: we cannot use --bare here because this host authenticates
        # via OAuth (Claude subscription), and --bare forces auth to
        # ANTHROPIC_API_KEY only. If/when an API key is provisioned, adding
        # --bare gives a further reduction (skips hooks/plugins entirely).
        #
        # NOTE on prompt caching: the Claude CLI runs as a one-shot
        # subprocess per call, so API-level prompt caching (cache_control)
        # is not available in this path. Migrating to the Anthropic Python
        # SDK would unlock it.
        result = subprocess.run(
            [
                'claude', '-p',
                '--model', 'haiku',
                '--system-prompt',
                'You are a topic classifier. Output ONLY a JSON array of '
                'category strings or null. No prose, no markdown, no '
                'explanation.',
                '--disable-slash-commands',
                '--tools', '',
                '--setting-sources', '',
                '--no-session-persistence',
                '--output-format', 'text',
            ],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT,
        )
        if result.returncode != 0:
            # 2026-04-14: log stdout too — claude CLI sometimes writes the
            # actual error to stdout (e.g. invalid model alias, auth error)
            # and leaves stderr empty, which made earlier failures invisible.
            logger.error(
                "Claude CLI failed (topic LLM): rc=%d stderr=%r stdout=%r",
                result.returncode,
                result.stderr[:300],
                result.stdout[:300],
            )
            # 2026-04-14: if the failure looks like a usage-limit / rate-
            # limit message, enter module-level backoff so subsequent
            # worker cycles skip the LLM pass instead of firing hundreds
            # more subprocess calls that will all fail the same way and
            # burn more quota. Other failure modes (transient network,
            # invalid JSON, etc.) still retry normally on the next cycle.
            if _is_quota_error(result.stdout, result.stderr):
                _quota_blocked_until = time.time() + QUOTA_BACKOFF_SECONDS
                resume_at = time.strftime(
                    '%Y-%m-%d %H:%M:%S',
                    time.localtime(_quota_blocked_until),
                )
                logger.warning(
                    "Topic LLM quota/rate-limit detected — entering "
                    "backoff for %d seconds. LLM pass will be skipped "
                    "until %s.",
                    QUOTA_BACKOFF_SECONDS,
                    resume_at,
                )
            return empty_results

        raw = result.stdout.strip()
    except FileNotFoundError:
        logger.error("Claude CLI not found — topic LLM pass unavailable")
        return empty_results
    except subprocess.TimeoutExpired:
        logger.error(f"Claude CLI timed out after {CLAUDE_TIMEOUT}s (topic LLM)")
        return empty_results

    # Strip markdown fences if present
    cleaned = raw
    if cleaned.startswith('```'):
        lines = cleaned.split('\n')
        lines = [ln for ln in lines if not ln.strip().startswith('```')]
        cleaned = '\n'.join(lines)

    # Parse JSON
    try:
        entries = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find('[')
        end = cleaned.rfind(']')
        if start != -1 and end != -1:
            try:
                entries = json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                logger.error(f"Failed to parse topic LLM response: {raw[:300]}")
                return empty_results
        else:
            logger.error(f"No JSON array in topic LLM response: {raw[:300]}")
            return empty_results

    # Validate length
    if len(entries) != len(batch):
        logger.warning(
            f"Topic LLM returned {len(entries)} entries for {len(batch)} items — discarding batch"
        )
        return empty_results

    # Validate each entry: must be a valid category string or None/null
    results: list[str | None] = []
    for entry in entries:
        if entry is None:
            results.append(None)
        elif isinstance(entry, str) and entry in VALID_CATEGORIES:
            results.append(entry)
        else:
            logger.debug(f"Topic LLM: invalid entry '{entry}' — treating as None")
            results.append(None)

    return results
