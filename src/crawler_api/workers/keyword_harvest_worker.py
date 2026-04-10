"""
Keyword harvest worker — weekly self-improving keyword extraction.

Extracts high-frequency named entities from LLM-classified TrendItems
(items the heuristic missed but LLM rescued), batch-confirms with Claude,
and writes surviving candidates to crawler/config/auto_keywords.json.

The topic_classifier.py heuristic layer loads auto_keywords.json at startup,
so new keywords take effect on next worker restart — no code deploy needed.

Schedule: weekly (Sunday 02:00 UTC)
  cron: 0 2 * * 0  python src/crawler_api/workers/keyword_harvest_worker.py --once

Usage:
  python keyword_harvest_worker.py          # run once and exit
  python keyword_harvest_worker.py --once   # same
"""

import json
import logging
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import django
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

from django.utils import timezone
from crawler_admin.models import TrendItem, SystemSettings

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-8s %(name)s  %(message)s',
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

AUTO_KEYWORDS_PATH = Path(__file__).parent.parent.parent.parent / 'config' / 'auto_keywords.json'

VALID_TOPICS = frozenset({
    'politics', 'finance', 'ai', 'tech', 'science', 'entertainment',
    'sports', 'business', 'crime', 'society', 'health', 'environment',
})

HARVEST_WINDOW_DAYS = 7
CLAUDE_TIMEOUT = 90

STOPWORDS_EN = {
    'The', 'This', 'That', 'New', 'Top', 'Big', 'US', 'UK', 'EU', 'UN',
    'A', 'An', 'In', 'On', 'At', 'Is', 'Are', 'Was', 'Were', 'Has', 'Have',
    'Had', 'Be', 'By', 'Of', 'To', 'As', 'For', 'Or', 'But', 'And', 'Not',
    'Its', 'It', 'He', 'She', 'They', 'We', 'You', 'How', 'Why', 'What',
    'When', 'Where', 'Who', 'Which', 'After', 'Before', 'Over', 'Under',
    'More', 'Most', 'Some', 'All', 'One', 'Two', 'First', 'Last', 'Next',
}

# Regex patterns for English entity extraction
_RE_ALLCAPS = re.compile(r'\b[A-Z]{2,6}\b')
_RE_TITLECASE = re.compile(r'\b(?:[A-Z][a-z]+\s){1,3}[A-Z][a-z]+\b')
_RE_CAMELCASE = re.compile(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b')   # TikTok, DeepSeek, ByteDance, YouTube
_RE_NOUN_VERB = re.compile(
    r'\b([A-Z][a-z]*(?:[A-Z][a-z]*)*[a-z]+)\s+'
    r'(?:said|announced|reported|reports|unveiled|banned|launched|warned|'
    r'pledged|signed|approved|filed|sued|wins|loses|beats|misses|cuts|raises|faces)\b'
)


# ---------------------------------------------------------------------------
# Step 1 — Find the gap: LLM-classified items the heuristic missed
# ---------------------------------------------------------------------------

def get_heuristic_missed_items(days: int = HARVEST_WINDOW_DAYS) -> list:
    """
    Items where:
      - topic_classified_at IS NOT NULL  (LLM ran on this item)
      - topic_tags != []                 (LLM successfully tagged it)

    These are items the heuristic missed but LLM rescued — the current
    blind spots of the keyword list.

    DEPENDENCY: This correctly isolates heuristic-missed items only because
    topic_classifier_worker.py runs LLM exclusively on items where
    topic_tags == [] after the heuristic pass. If that constraint changes,
    this query must be updated accordingly.
    """
    cutoff = timezone.now() - timedelta(days=days)
    items = (
        TrendItem.objects
        .filter(
            collected_at__gte=cutoff,
            topic_classified_at__isnull=False,
        )
        .exclude(topic_tags=[])
        .select_related('surface')
        .only('id', 'title_original', 'canonical_title', 'topic_tags', 'lang_group')
    )
    result = list(items)
    logger.info(f"Step 1: found {len(result)} LLM-classified items in last {days} days")
    return result


# ---------------------------------------------------------------------------
# Step 2 — Extract named entities from titles
# ---------------------------------------------------------------------------

def extract_english_entities(title: str) -> list[str]:
    """Extract named entities from an English title using regex patterns."""
    entities = set()

    # Pattern A: ALL-CAPS abbreviations (2-6 chars): PBOC, TSMC, BYD
    for m in _RE_ALLCAPS.finditer(title):
        entities.add(m.group())

    # Pattern B: Title-cased multi-word phrases: "South China Sea", "Federal Reserve"
    for m in _RE_TITLECASE.finditer(title):
        entities.add(m.group().strip())

    # Pattern C: Proper noun (any case) followed by action verb
    for m in _RE_NOUN_VERB.finditer(title):
        entities.add(m.group(1))

    # Pattern D: CamelCase compound words: TikTok, DeepSeek, ByteDance, YouTube
    for m in _RE_CAMELCASE.finditer(title):
        entities.add(m.group())

    # Apply stopword filter and min-length filter
    return [e for e in entities if e not in STOPWORDS_EN and len(e) >= 2]


def extract_chinese_entities(title: str) -> list[str]:
    """Extract nouns from a Chinese title using jieba POS tagging."""
    try:
        import jieba.posseg as pseg
    except ImportError:
        logger.warning("jieba not installed — skipping Chinese title extraction")
        return []

    CHINESE_NOUN_FLAGS = {'n', 'nr', 'ns', 'nt', 'nz'}
    words = pseg.cut(title)
    return [
        w.word.strip()
        for w in words
        if w.flag in CHINESE_NOUN_FLAGS and len(w.word.strip()) >= 2
    ]


def extract_entities_from_items(items: list) -> dict[str, dict[str, dict[str, int]]]:
    """
    For each item, extract entities from title and count per (entity, topic).

    Returns: {entity: {topic: count}}
    Multi-label items (e.g. topic_tags=["politics","tech"]) count the entity
    once under EACH label it appears in.
    """
    entity_topic_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for item in items:
        title = item.canonical_title or item.title_original or ''
        if not title:
            continue

        lang = item.lang_group or 'en'
        if lang == 'zh':
            entities = extract_chinese_entities(title)
        else:
            entities = extract_english_entities(title)

        topic_tags = item.topic_tags or []
        for entity in entities:
            for topic in topic_tags:
                if topic in VALID_TOPICS:
                    entity_topic_counts[entity][topic] += 1

    logger.info(f"Step 2: extracted {len(entity_topic_counts)} unique entities")
    return dict(entity_topic_counts)


# ---------------------------------------------------------------------------
# Step 3 — Frequency + purity filters
# ---------------------------------------------------------------------------

def apply_filters(
    entity_topic_counts: dict[str, dict[str, int]],
    min_freq: int,
) -> list[dict]:
    """
    Promote entity to candidate keyword only if:
      - Total occurrences >= min_freq
      - >= 80% of occurrences are in the primary topic
        (primary = most frequent topic; ties broken by sorted VALID_TOPICS order)

    Returns list of {term, context_topic, total_count, purity} dicts.
    """
    candidates = []
    for entity, topic_counts in entity_topic_counts.items():
        total = sum(topic_counts.values())
        if total < min_freq:
            continue

        # Primary topic: mode; ties broken by sorted order for determinism
        primary_topic = max(
            topic_counts,
            key=lambda t: (topic_counts[t], -sorted(VALID_TOPICS).index(t) if t in sorted(VALID_TOPICS) else 0),
        )
        primary_count = topic_counts[primary_topic]
        purity = primary_count / total

        if purity < 0.80:
            continue

        candidates.append({
            'term': entity,
            'context_topic': primary_topic,
            'total_count': total,
            'purity': round(purity, 3),
        })

    logger.info(f"Step 3: {len(candidates)} candidates passed freq/purity filters (min_freq={min_freq})")
    return candidates


# ---------------------------------------------------------------------------
# Step 4 — LLM batch confirmation
# ---------------------------------------------------------------------------

def _parse_llm_json(raw: str) -> list | None:
    """Extract JSON array from LLM response, stripping markdown fences."""
    cleaned = raw.strip()
    if cleaned.startswith('```'):
        lines = [l for l in cleaned.split('\n') if not l.strip().startswith('```')]
        cleaned = '\n'.join(lines)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find('['), cleaned.rfind(']')
        if start != -1 and end != -1:
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                pass
    return None


def llm_confirm_candidates(candidates: list[dict]) -> list[dict]:
    """
    Send candidates to Claude in a single batch for topic confirmation.

    Discard any where:
      - confirmed_topic not in VALID_TOPICS (invalid label)
      - confirmed_topic != context_topic (LLM disagrees → cross-topic, discard)

    NOTE ON LLM AUTHORITY: Surviving candidates are treated as authoritative.
    auto_keywords.json should be reviewed monthly via git diff to guard against
    systematic LLM misclassification.

    Returns filtered list of confirmed candidates.
    """
    if not candidates:
        return []

    term_lines = json.dumps(
        [{'term': c['term'], 'context_topic': c['context_topic']} for c in candidates],
        ensure_ascii=False,
        indent=2,
    )

    prompt = f"""The following terms appear frequently in news articles that were classified by topic.
For each term, confirm the best topic label from this fixed set:
politics, finance, ai, tech, science, entertainment, sports, business, crime, society, health, environment

Terms (with the topic they appeared under most often):
{term_lines}

Return ONLY a JSON array with one entry per term, in the same order.
Each entry: {{"term": "...", "confirmed_topic": "..."}}
Do not include confidence scores or any other fields.
Return exactly {len(candidates)} entries."""

    try:
        result = subprocess.run(
            ['claude', '-p', '--model', 'sonnet'],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT,
        )
        if result.returncode != 0:
            logger.error(f"Claude CLI failed (returncode={result.returncode}): {result.stderr[:300]}")
            return []
        raw = result.stdout.strip()
    except FileNotFoundError:
        logger.error("Claude CLI not found — skipping LLM confirmation step")
        return []
    except subprocess.TimeoutExpired:
        logger.error(f"Claude CLI timed out after {CLAUDE_TIMEOUT}s — skipping LLM confirmation")
        return []

    entries = _parse_llm_json(raw)
    if entries is None:
        logger.error(f"Failed to parse LLM response: {raw[:300]}")
        return []

    if len(entries) != len(candidates):
        logger.warning(
            f"LLM returned {len(entries)} entries for {len(candidates)} candidates — discarding batch"
        )
        return []

    confirmed = []
    for candidate, entry in zip(candidates, entries):
        if not isinstance(entry, dict):
            continue
        confirmed_topic = entry.get('confirmed_topic', '').strip()
        if confirmed_topic not in VALID_TOPICS:
            logger.debug(f"Discarding '{candidate['term']}': invalid topic '{confirmed_topic}'")
            continue
        if confirmed_topic != candidate['context_topic']:
            logger.debug(
                f"Discarding '{candidate['term']}': LLM topic '{confirmed_topic}' "
                f"!= context topic '{candidate['context_topic']}'"
            )
            continue
        confirmed.append(candidate)

    logger.info(
        f"Step 4: {len(confirmed)}/{len(candidates)} candidates confirmed by LLM"
    )
    return confirmed


# ---------------------------------------------------------------------------
# Step 5 — Write auto_keywords.json
# ---------------------------------------------------------------------------

def write_auto_keywords(confirmed: list[dict]) -> None:
    """
    Full regeneration of auto_keywords.json from confirmed candidates.

    File is overwritten each run — not appended. Stale keywords from
    topics that fell out of relevance are automatically removed.
    Git history provides the audit trail.
    """
    keywords: dict[str, list[str]] = defaultdict(list)
    for c in confirmed:
        keywords[c['context_topic']].append(c['term'])

    output = {
        'generated_at': timezone.now().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'keywords': dict(keywords),
    }

    AUTO_KEYWORDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(AUTO_KEYWORDS_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    total = sum(len(v) for v in keywords.values())
    logger.info(
        f"Step 5: wrote {total} keywords across {len(keywords)} topics "
        f"to {AUTO_KEYWORDS_PATH}"
    )
    for topic, terms in sorted(keywords.items()):
        logger.info(f"  {topic}: {terms}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_harvest() -> None:
    logger.info("=== Keyword harvest job starting ===")

    # Read configurable frequency threshold from admin settings
    min_freq = int(SystemSettings.get_setting('harvest_min_freq', default=20))
    logger.info(f"Config: HARVEST_WINDOW_DAYS={HARVEST_WINDOW_DAYS}, min_freq={min_freq}")

    # Step 1 — find LLM-rescued items
    items = get_heuristic_missed_items(days=HARVEST_WINDOW_DAYS)
    if not items:
        logger.info("No LLM-classified items found — nothing to harvest")
        return

    # Step 2 — extract entities
    entity_topic_counts = extract_entities_from_items(items)
    if not entity_topic_counts:
        logger.info("No entities extracted — exiting")
        return

    # Step 3 — frequency + purity filters
    candidates = apply_filters(entity_topic_counts, min_freq=min_freq)
    if not candidates:
        logger.info("No candidates passed filters — exiting")
        return

    # Step 4 — LLM batch confirmation
    confirmed = llm_confirm_candidates(candidates)
    if not confirmed:
        logger.info("No candidates confirmed by LLM — exiting")
        return

    # Step 5 — write output
    write_auto_keywords(confirmed)

    logger.info("=== Keyword harvest job complete ===")


if __name__ == '__main__':
    run_harvest()
