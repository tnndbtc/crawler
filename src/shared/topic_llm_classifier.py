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

from shared.topic_classifier import VALID_CATEGORIES

logger = logging.getLogger(__name__)

BATCH_SIZE = 20
CLAUDE_TIMEOUT = 90

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

    # Build prompt
    item_lines = []
    for i, item in enumerate(batch, 1):
        title = (item.get('title') or '')[:200]
        platform = item.get('platform') or 'unknown'
        desc = (item.get('description') or '')[:200]
        line = f"{i}. [{platform}] {title}"
        if desc:
            line += f" — {desc}"
        item_lines.append(line)

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
        result = subprocess.run(
            ['claude', '-p', '--model', 'sonnet'],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT,
        )
        if result.returncode != 0:
            logger.error(f"Claude CLI failed (topic LLM): {result.stderr[:300]}")
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
