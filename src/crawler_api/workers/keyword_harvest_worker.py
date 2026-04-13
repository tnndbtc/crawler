"""
Keyword harvest worker — hourly self-improving keyword extraction.

Extracts named entities from LLM-classified TrendItems (items the heuristic
missed but LLM rescued), batch-confirms with Claude, and merges surviving
candidates into crawler/config/auto_keywords.json.

Key design decisions:
  - Incremental window: each run queries only articles since last_harvested_at,
    so every article is counted exactly once (no score inflation).
  - Accumulate + score-based expiry: keywords are never fully overwritten.
    Each keyword carries a cumulative score and last_seen date.
    Weak keywords (score < STRONG_THRESHOLD) expire after 30 days of silence.
    Strong keywords (score >= STRONG_THRESHOLD) expire after 90 days.
  - classified_by filter: only LLM-classified items are sourced, preventing
    heuristic-rescued items from polluting keyword learning after a reset.

topic_classifier_worker hot-reloads auto_keywords.json each cycle (no restart
needed). On reload it resets "Tried, no tags" items so heuristic gets a second
pass before LLM — saving LLM cost over time.

Schedule: hourly
  cron: 0 * * * *  python src/crawler_api/workers/keyword_harvest_worker.py --once

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

# Canonical 9-value story_category vocabulary
VALID_TOPICS = frozenset({
    'world', 'politics', 'business', 'technology', 'ai',
    'science', 'society', 'sports', 'entertainment',
})

CLAUDE_TIMEOUT = 90

# Expiry thresholds
DEFAULT_STRONG_THRESHOLD = 10   # score >= this → "strong" keyword
DEFAULT_EXPIRE_DAYS = 30        # weak keywords: expire after N days of silence
DEFAULT_STRONG_EXPIRE_DAYS = 90 # strong keywords: expire after N days of silence

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

def get_llm_classified_items(cutoff_start, cutoff_end) -> list:
    """
    Items classified by LLM since the last harvest run.

    Uses classified_by='llm' to source only genuine LLM rescues — heuristic-
    rescued items are excluded even if they were reset and re-classified after
    a keyword hot-reload. This keeps keyword learning signal pure.

    cutoff_start: timezone-aware datetime (last_harvested_at from SystemSettings)
    cutoff_end:   timezone-aware datetime (now, captured before query)
    """
    items = (
        TrendItem.objects
        .filter(
            collected_at__gt=cutoff_start,
            collected_at__lte=cutoff_end,
            classified_by='llm',
        )
        .exclude(story_category__isnull=True)
        .only('id', 'title_original', 'canonical_title', 'story_category', 'lang_group')
    )
    result = list(items)
    logger.info(
        f"Step 1: found {len(result)} LLM-classified items "
        f"between {cutoff_start.isoformat()} and {cutoff_end.isoformat()}"
    )
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
    For each item, extract entities from title and count per (entity, story_category).

    Returns: {entity: {story_category: count}}
    Uses story_category (single value) instead of topic_tags (multi-label).
    """
    entity_topic_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for item in items:
        title = item.canonical_title or item.title_original or ''
        if not title:
            continue

        category = getattr(item, 'story_category', None)
        if not category or category not in VALID_TOPICS:
            continue

        lang = item.lang_group or 'en'
        if lang == 'zh':
            entities = extract_chinese_entities(title)
        else:
            entities = extract_english_entities(title)

        for entity in entities:
            entity_topic_counts[entity][category] += 1

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
world, politics, business, technology, ai, science, society, sports, entertainment

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
# Step 5 — Load existing keywords + merge + expire + write
# ---------------------------------------------------------------------------

def load_existing_keywords() -> dict[str, dict[str, dict]]:
    """
    Load auto_keywords.json and return existing keyword state as:
      {topic: {term: {"last_seen": "YYYY-MM-DD", "score": N}}}

    Handles three entry formats (backwards compatible):
      - Plain string:            "Zelensky"
      - Old object (no score):   {"term": "Zelensky", "last_seen": "..."}
      - New object (with score): {"term": "Zelensky", "last_seen": "...", "score": N}

    Missing file → returns {} (first run, safe).
    Corrupt/unreadable file → logs warning, returns {} (safe fallback).
    """
    today = timezone.now().strftime('%Y-%m-%d')
    try:
        with open(AUTO_KEYWORDS_PATH, encoding='utf-8') as f:
            data = json.load(f)
        raw = data.get('keywords', {})
        result: dict[str, dict[str, dict]] = {}
        for topic, entries in raw.items():
            topic_kws: dict[str, dict] = {}
            for entry in entries:
                if isinstance(entry, str):
                    topic_kws[entry] = {'last_seen': today, 'score': 1}
                elif isinstance(entry, dict):
                    term = entry.get('term')
                    if term and isinstance(term, str):
                        topic_kws[term] = {
                            'last_seen': entry.get('last_seen', today),
                            'score': int(entry.get('score', 1)),
                        }
            result[topic] = topic_kws
        logger.info(
            f"Loaded {sum(len(v) for v in result.values())} existing keywords "
            f"across {len(result)} topics from {AUTO_KEYWORDS_PATH}"
        )
        return result
    except FileNotFoundError:
        logger.info("auto_keywords.json not found — starting fresh (first run)")
        return {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"auto_keywords.json unreadable ({e}); starting from empty state")
        return {}


def write_auto_keywords(confirmed: list[dict], cutoff_end) -> None:
    """
    Merge newly confirmed candidates into existing auto_keywords.json.

    For each confirmed candidate:
      - If term already exists: score += new_count, last_seen = today
      - If new term: score = new_count, last_seen = today

    Then apply two-tier expiry:
      - score >= STRONG_THRESHOLD: drop if silent > strong_expire_days
      - score <  STRONG_THRESHOLD: drop if silent > expire_days

    Writes merged result back to file (not a full overwrite — accumulates
    over time so keywords survive quiet periods).
    """
    today = timezone.now().strftime('%Y-%m-%d')

    strong_threshold = int(SystemSettings.get_setting(
        'harvest_strong_threshold', default=DEFAULT_STRONG_THRESHOLD))
    expire_days = int(SystemSettings.get_setting(
        'harvest_expire_days', default=DEFAULT_EXPIRE_DAYS))
    strong_expire_days = int(SystemSettings.get_setting(
        'harvest_strong_expire_days', default=DEFAULT_STRONG_EXPIRE_DAYS))

    # Load existing state
    existing = load_existing_keywords()

    # Merge confirmed candidates into existing state
    added = 0
    updated = 0
    for c in confirmed:
        topic = c['context_topic']
        term = c['term']
        new_count = c['total_count']

        if topic not in existing:
            existing[topic] = {}

        if term in existing[topic]:
            existing[topic][term]['score'] += new_count
            existing[topic][term]['last_seen'] = today
            updated += 1
        else:
            existing[topic][term] = {'last_seen': today, 'score': new_count}
            added += 1

    logger.info(f"Step 5 merge: {added} new keywords added, {updated} existing updated")

    # Apply expiry — two-tier based on score
    from datetime import date
    today_date = date.today()
    expired = 0
    for topic in list(existing.keys()):
        surviving = {}
        for term, kw in existing[topic].items():
            try:
                last_seen_date = date.fromisoformat(kw['last_seen'])
            except (ValueError, KeyError):
                last_seen_date = today_date  # malformed → treat as seen today
            silence_days = (today_date - last_seen_date).days
            threshold = strong_expire_days if kw['score'] >= strong_threshold else expire_days
            if silence_days <= threshold:
                surviving[term] = kw
            else:
                expired += 1
        existing[topic] = surviving

    if expired:
        logger.info(f"Step 5 expiry: dropped {expired} keywords past silence threshold")

    # Serialise to list format
    output_keywords: dict[str, list] = {}
    for topic, kws in existing.items():
        if kws:  # skip empty topics
            output_keywords[topic] = [
                {'term': term, 'last_seen': kw['last_seen'], 'score': kw['score']}
                for term, kw in sorted(kws.items())
            ]

    output = {
        'generated_at': timezone.now().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'keywords': output_keywords,
    }

    AUTO_KEYWORDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(AUTO_KEYWORDS_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    total = sum(len(v) for v in output_keywords.values())
    logger.info(
        f"Step 5: wrote {total} keywords across {len(output_keywords)} topics "
        f"to {AUTO_KEYWORDS_PATH}"
    )
    for topic, entries in sorted(output_keywords.items()):
        terms = [e['term'] for e in entries]
        logger.info(f"  {topic}: {terms}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_harvest() -> None:
    logger.info("=== Keyword harvest job starting ===")

    # Incremental window: only articles since last harvest run
    cutoff_end = timezone.now()
    last_harvested_raw = SystemSettings.get_setting('last_harvested_at', default=None)
    if last_harvested_raw:
        from django.utils.dateparse import parse_datetime
        cutoff_start = parse_datetime(last_harvested_raw)
        if cutoff_start is None:
            # Malformed value — fall back to 1 hour ago
            logger.warning(f"Could not parse last_harvested_at='{last_harvested_raw}'; using 1h ago")
            cutoff_start = cutoff_end - timedelta(hours=1)
    else:
        # First ever run — look back 1 hour
        cutoff_start = cutoff_end - timedelta(hours=1)
        logger.info("No last_harvested_at found — first run, looking back 1 hour")

    min_freq = int(SystemSettings.get_setting('harvest_min_freq', default=1))
    logger.info(
        f"Config: cutoff_start={cutoff_start.isoformat()}, "
        f"cutoff_end={cutoff_end.isoformat()}, min_freq={min_freq}"
    )

    # Step 1 — find LLM-rescued items in incremental window
    items = get_llm_classified_items(cutoff_start, cutoff_end)
    if not items:
        logger.info("No LLM-classified items in window — nothing to harvest")
        # Still update last_harvested_at so next run doesn't re-scan this window
        SystemSettings.set_setting(
            'last_harvested_at', cutoff_end.isoformat(),
            description='Timestamp of last keyword harvest run',
            value_type='string',
        )
        return

    # Step 2 — extract entities
    entity_topic_counts = extract_entities_from_items(items)
    if not entity_topic_counts:
        logger.info("No entities extracted — exiting")
        SystemSettings.set_setting(
            'last_harvested_at', cutoff_end.isoformat(),
            description='Timestamp of last keyword harvest run',
            value_type='string',
        )
        return

    # Step 3 — frequency + purity filters
    candidates = apply_filters(entity_topic_counts, min_freq=min_freq)
    if not candidates:
        logger.info("No candidates passed filters — exiting")
        SystemSettings.set_setting(
            'last_harvested_at', cutoff_end.isoformat(),
            description='Timestamp of last keyword harvest run',
            value_type='string',
        )
        return

    # Step 4 — LLM batch confirmation
    confirmed = llm_confirm_candidates(candidates)
    if not confirmed:
        logger.info("No candidates confirmed by LLM — exiting")
        SystemSettings.set_setting(
            'last_harvested_at', cutoff_end.isoformat(),
            description='Timestamp of last keyword harvest run',
            value_type='string',
        )
        return

    # Step 5 — merge into existing auto_keywords.json
    write_auto_keywords(confirmed, cutoff_end)

    # Update last_harvested_at AFTER successful write
    SystemSettings.set_setting(
        'last_harvested_at', cutoff_end.isoformat(),
        description='Timestamp of last keyword harvest run',
        value_type='string',
    )

    logger.info("=== Keyword harvest job complete ===")


if __name__ == '__main__':
    run_harvest()
