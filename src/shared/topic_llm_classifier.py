"""
Topic LLM classifier — Phase 4.

Classifies TrendItems by topic using Claude CLI when the heuristic pass
(topic_classifier.py) returns no tags.

Interface required by topic_classifier_worker.py:
    classify_batch_llm(items: list[dict]) -> list[list[str]]
    BATCH_SIZE: int

items dicts have keys: 'title', 'platform', 'description' (max 300 chars).

Returns a list of tag lists, one per input item. Each tag list contains
zero or more labels from VALID_TOPICS. Empty list means "unclassifiable"
(worker marks topic_classified_at without setting tags).
"""

import json
import logging
import subprocess

from shared.topic_classifier import VALID_TOPICS

logger = logging.getLogger(__name__)

BATCH_SIZE = 20
CLAUDE_TIMEOUT = 90


def classify_batch_llm(items: list[dict]) -> list[list[str]]:
    """
    Classify a batch of items using Claude CLI.

    Args:
        items: list of dicts with 'title', 'platform', 'description' keys.
               Max BATCH_SIZE items — caller is responsible for batching.

    Returns:
        List of tag-lists, one per item. Each tag list contains zero or more
        validated labels from VALID_TOPICS. On total failure, returns
        list of [] for all items (safe no-op — worker marks as attempted).
    """
    if not items:
        return []

    batch = items[:BATCH_SIZE]
    empty_results: list[list[str]] = [[] for _ in batch]

    valid_labels = sorted(VALID_TOPICS)

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

    prompt = f"""For each news item below, assign one or more topic labels.

Valid labels: {', '.join(valid_labels)}

Rules:
- Choose only labels from the list above.
- Use multiple labels when genuinely applicable (e.g. an AI chip tariff story: ["ai", "finance", "politics"]).
- Return [] if none apply (e.g. purely local gossip or unclear content).
- Do NOT return labels not in the list.

Items:
{chr(10).join(item_lines)}

Return ONLY a JSON array of {len(batch)} entries. Each entry is a list of label strings.
Example for 4 items: [["tech", "ai"], ["politics"], [], ["finance"]]

CRITICAL: Return exactly {len(batch)} entries. Each entry MUST be a list."""

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

    # Validate structure and filter to valid labels
    results: list[list[str]] = []
    for entry in entries:
        if not isinstance(entry, list):
            results.append([])
            continue
        valid = sorted({
            tag for tag in entry
            if isinstance(tag, str) and tag in VALID_TOPICS
        })
        results.append(valid)

    return results
