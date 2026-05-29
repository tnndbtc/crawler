"""
Content-based region classifier — LLM pass (fallback for heuristic misses).

Batches 20 items per Claude call. Multi-label output.
All LLM output is validated before storage.
"""

import json
import logging
import subprocess

logger = logging.getLogger(__name__)

VALID_REGIONS = {
    'us', 'cn', 'jp', 'kr', 'de', 'fr', 'br', 'es', 'ru', 'in',
    'it', 'tr', 'ar', 'id', 'pl', 'nl', 'se', 'ph', 'vn', 'th',
    'my', 'pt', 'ar_latam', 'tw', 'hk',
}

BATCH_SIZE = 20
CLAUDE_TIMEOUT = 60


def classify_batch_llm(items: list[dict]) -> list[tuple[list[str], str | None]]:
    """
    Classify a batch of items using Claude CLI.

    Args:
        items: list of dicts with 'title' and 'platform' keys.
               Max BATCH_SIZE items.

    Returns:
        List of (content_regions, primary_region) tuples, one per item.
        On failure, returns list of ([], None) for all items.
    """
    if not items:
        return []

    batch = items[:BATCH_SIZE]
    empty_results = [([], None)] * len(batch)

    # Build prompt
    item_lines = []
    for i, item in enumerate(batch, 1):
        title = item.get('title', '')[:200]
        platform = item.get('platform', 'unknown')
        item_lines.append(f"{i}. Title: {title}  Platform: {platform}")

    prompt = f"""For each item below, determine which countries/regions the content is
ABOUT. An article can be about multiple regions (e.g., "US-China trade war"
is about both US and China).

Use these codes: us, cn, jp, kr, de, fr, br, es, ru, in, it, tr,
ar, id, pl, nl, se, ph, vn, th, my, pt, ar_latam, tw, hk
Use "none" if no specific region. Use "none" if unclear.

Items:
{chr(10).join(item_lines)}

Return ONLY a JSON array of {len(batch)} entries. Each entry is a list of region codes.
Example: [["cn", "us"], ["none"], ["jp"], ["de", "fr"], ...]

CRITICAL: Return exactly {len(batch)} entries. Each entry MUST be a list."""

    # Call Claude
    # 2026-04-14: strip Claude Code harness overhead on every subprocess.
    # Each `claude -p` call was loading the full default system prompt,
    # all skill manifests, the deferred tool catalogue, user CLAUDE.md,
    # hooks, and per-machine env sections — ~15-25 KB of input tokens per
    # call that this classifier never uses. The flags below reduce each
    # call to just the model + our prompt:
    #   --system-prompt           replaces multi-KB default with one-liner
    #   --disable-slash-commands  skips loading every skill manifest
    #   --tools ""                drops all tool schemas (pure text in/out)
    #   --setting-sources user    skips project CLAUDE.md walk
    #   --no-session-persistence  no session file writes for one-shot
    # Same pattern as topic_llm_classifier.py (commit 7163c925).
    try:
        result = subprocess.run(
            [
                'claude', '-p',
                '--model', 'haiku',
                '--system-prompt',
                'You are a region classifier. Output ONLY a JSON array of '
                'region-code lists. No prose, no markdown, no explanation.',
                '--disable-slash-commands',
                '--tools', '',
                '--setting-sources', 'user',
                '--no-session-persistence',
                '--output-format', 'text',
            ],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT,
        )
        if result.returncode != 0:
            logger.error(f"Claude CLI failed: {result.stderr[:200]}")
            return empty_results

        raw = result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.error(f"Claude CLI error: {e}")
        return empty_results

    # Parse JSON
    # Strip markdown fences if present
    cleaned = raw
    if cleaned.startswith('```'):
        lines = cleaned.split('\n')
        lines = [l for l in lines if not l.strip().startswith('```')]
        cleaned = '\n'.join(lines)

    try:
        entries = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to find JSON array in response
        start = cleaned.find('[')
        end = cleaned.rfind(']')
        if start != -1 and end != -1:
            try:
                entries = json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                logger.error(f"Failed to parse LLM region response: {raw[:300]}")
                return empty_results
        else:
            logger.error(f"No JSON array in LLM response: {raw[:300]}")
            return empty_results

    # Validate: length must match batch
    if len(entries) != len(batch):
        logger.warning(
            f"LLM returned {len(entries)} entries for {len(batch)} items — discarding batch"
        )
        return empty_results

    # Validate: every entry must be a list
    if not all(isinstance(e, list) for e in entries):
        logger.warning("LLM returned non-list entries — discarding batch")
        return empty_results

    # Validate and convert each entry
    results = []
    for entry in entries:
        # Filter to valid region codes
        regions = [c for c in entry if isinstance(c, str) and c in VALID_REGIONS]

        if not regions:
            results.append(([], None))
        else:
            regions_sorted = sorted(set(regions))
            non_us = [r for r in regions_sorted if r != 'us']
            primary = non_us[0] if non_us else regions_sorted[0]
            results.append((regions_sorted, primary))

    return results
