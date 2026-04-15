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
import subprocess
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import shared.extractors  # noqa: F401 — populates EXTRACTORS dispatch table
from shared.extractors import EXTRACTORS, DEFAULT_EXTRACTOR

import django
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

# ---------------------------------------------------------------------------
# Noise-keyword filter (2026-04-14)
# ---------------------------------------------------------------------------
#
# Post-extraction junk filter applied to candidate terms before they reach
# the LLM confirmation step or get persisted to auto_keywords.json.
#
# Purpose: kill low-signal / noise terms like "BOTH", "II", "CL", "HE" that
# leak out of the regex-based entity extractors. These terms pollute the
# heuristic classifier (Signal 3) and degrade downstream story_engine
# output. Filtering here ALSO saves LLM confirmation cost: junk terms get
# dropped before `llm_confirm_candidates` spends quota asking Claude what
# topic "BOTH" belongs to.
#
# This is intentionally a pure-Python deterministic filter — no NLP model,
# no LLM, no external dependencies. Rules are independent and cheap.

# Case-insensitive reject set: grammatical stopwords (rule 2) merged with
# low-signal generic news words (rule 5). Stored lowercase; the predicate
# lowercases before lookup.
#
# Expanded 2026-04-14 after dry-run against real auto_keywords.json showed
# function words like "INTO" leaking through the all-caps regex extractor
# into persisted keywords. The list below covers common English function
# words ≥3 chars (shorter ones are already killed by rule 1). Terms only
# match when the ENTIRE normalized keyword equals one of these — so
# phrases like "New Mexico" or "Into the Wild" still pass because their
# full form isn't in the set.
_KEYWORD_FILTER_STOPWORDS: frozenset[str] = frozenset({
    # --- Articles / determiners ---
    "the", "a", "an", "this", "that", "these", "those",
    # --- Conjunctions ---
    "and", "or", "but", "both", "nor", "yet",
    # --- Prepositions ---
    "for", "from", "with", "without", "into", "onto", "upon",
    "about", "after", "before", "over", "under", "through",
    "during", "between", "against",
    # --- Pronouns / possessives ---
    "he", "she", "it", "its", "they", "them", "their",
    "we", "us", "you", "your", "our", "his", "her",
    # --- Auxiliaries / common verbs ---
    "is", "are", "was", "were", "been", "being",
    "has", "have", "had", "does", "did",
    "will", "would", "could", "should", "can", "may", "might",
    # --- Wh-words ---
    "how", "why", "what", "when", "where", "who", "which", "whom",
    # --- Quantifiers / adverbs ---
    "all", "any", "some", "more", "most", "many", "much",
    "also", "only", "just", "very", "too", "then", "than", "such",
    # --- Low-value adjectives that appear as orphan title-case tokens ---
    "new", "top", "big", "old",
    # Rule 5 — low-signal generic news vocabulary
    "news", "video", "update", "report", "today",
    "breaking", "latest", "live", "watch",
})


def _normalize_term(term: str) -> str:
    """Rule 7 — trim outer whitespace and collapse internal runs of
    whitespace to a single space. Applied before any predicate check so
    filter rules see a canonical form."""
    return ' '.join(term.split())


def is_valid_keyword(term: str, score: int, lang: str | None = None) -> bool:
    """
    Return True if `term` should be admitted as a new auto-keyword.

    Applied BEFORE any keyword is written into auto_keywords.json. Rules
    are documented by number to match the filter spec; order runs
    cheapest-first so common rejects short-circuit quickly.

    Args:
        term:  Raw candidate term from the entity extractor.
        score: Observation count for this term in the current harvest
               window (the `total_count` field from apply_filters).

    Returns:
        True  — term is non-noise and meets the score floor; safe to
                pass downstream to LLM confirmation + auto_keywords.json.
        False — term is noise (reject).
    """
    # Rule 7 — normalize first so length / case checks see canonical form
    term = _normalize_term(term)

    # Rule 1 — minimum length (after normalization). Context-aware:
    # ASCII terms need >=3 chars (kills English 2-char noise like "II",
    # "CL", "HE"), but non-ASCII terms only need >=2 chars because CJK
    # (Chinese/Japanese/Korean) is semantically denser per character —
    # legitimate 2-character words like "醫療" (healthcare), "警方"
    # (police), "经济" (economy) would be lost with a universal >=3.
    _NO_CASE_SCRIPTS = {'ja', 'ko', 'ar', 'th', 'hi', 'zh'}
    min_len = 3 if (term.isascii() and lang not in _NO_CASE_SCRIPTS) else 2
    if len(term) < min_len:
        return False

    # Rule 6 — score threshold. Requires repeated observation; one-off
    # spurious extractions get dropped even if everything else passes.
    if score < 2:
        return False

    # Rules 2 + 5 — stopword / low-signal word (case-insensitive)
    if term.lower() in _KEYWORD_FILTER_STOPWORDS:
        return False

    # Rule 3 — all-caps short tokens like "II", "CL", "TP", "HE" —
    # almost always noise. `isalpha()` guard is important: Python's
    # `str.isupper()` ignores non-cased characters, so without the
    # alpha check "U.S." would match isupper()==True and get wrongly
    # rejected here. Requiring isalpha() restricts rule 3 to pure
    # alphabetic all-caps tokens and lets "U.S." fall through to the
    # alnum-ratio and capitalization checks below, which it passes.
    #
    # Threshold tuned from <=4 to <=2 on 2026-04-14 after dry-run
    # analysis against real auto_keywords.json showed the <=4 bound
    # was wrongly rejecting many legitimate 3-4 char acronyms — BJP,
    # CDU, NDP, PKK, RFK, STF, HIV, PSG, WSJ, TMZ, TDK, OLED, GTX,
    # BLIK, NASA, OPEC and more. Dropping to <=2 still catches every
    # example in the original spec ("II", "CL", "TP", "HE" are all
    # 2 chars) while preserving real proper-noun acronyms. At <=2
    # this rule is effectively redundant with rule 1 for ASCII input
    # (any 2-char ASCII term is already killed by len<3), but keeping
    # it is harmless defense-in-depth and documents the intent.
    if term.isalpha() and term.isupper() and len(term) <= 2:
        return False

    # Rule 4 — non-alphanumeric / punctuation noise. Require at least
    # half of the characters to be letters or digits. Passes "U.S."
    # (2 alnum / 4 total = 0.50), rejects "..." (0/3) and "///" (0/3).
    alnum = sum(1 for c in term if c.isalnum())
    if alnum / len(term) < 0.5:
        return False

    # Rule 8 (soft / optional) — require at least one capitalized word.
    # This is the simplest interpretation of "prefer Title Case" that
    # avoids false rejects on mixed-case names: "iPhone" fails here but
    # "Nancy Pelosi", "OpenAI", "GameStop", "Tiger Woods", "New York"
    # all pass. Generic lowercase words like "something", "breaking",
    # "update" get dropped. Single-letter splits are skipped to avoid
    # IndexError on degenerate input.
    #
    # ASCII guard: rule 8 only applies to pure-ASCII terms. CJK
    # characters (Chinese/Japanese/Korean) have no case concept, so
    # `w[0].isupper()` always returns False for them — without this
    # guard rule 8 would reject every Chinese keyword unconditionally
    # even though the extractor produces legitimate terms like '醫療'
    # or '警方'. Non-ASCII terms are judged by rules 1-7 only.
    if lang not in _NO_CASE_SCRIPTS and term.isascii() and not any(w and w[0].isupper() for w in term.split()):
        return False

    return True


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

def extract_entities_from_items(items: list) -> dict[tuple[str, str], dict[str, int]]:
    """
    For each item, extract entities from title and count per (entity, lang, story_category).

    Returns: {(entity, lang): {story_category: count}}
    """
    entity_topic_counts: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    lang_counters: dict[str, int] = defaultdict(int)

    for item in items:
        title = item.canonical_title or item.title_original or ''
        if not title:
            continue

        category = getattr(item, 'story_category', None)
        if not category or category not in VALID_TOPICS:
            continue

        lang = item.lang_group or 'en'
        extractor = EXTRACTORS.get(lang, DEFAULT_EXTRACTOR)
        entities = extractor(title, lang)

        for entity in entities:
            entity_topic_counts[(entity, lang)][category] += 1
        lang_counters[lang] += len(entities)

    lang_detail = ', '.join(f'{k}={v}' for k, v in sorted(lang_counters.items()))
    logger.info(f"Step 2: extracted {len(entity_topic_counts)} unique entities ({lang_detail})")
    return dict(entity_topic_counts)


# ---------------------------------------------------------------------------
# Step 3 — Frequency + purity filters
# ---------------------------------------------------------------------------

def apply_filters(
    entity_topic_counts: dict[tuple[str, str], dict[str, int]],
    min_freq: int,
) -> list[dict]:
    """
    Promote entity to candidate keyword only if:
      - Total occurrences >= min_freq
      - >= 80% of occurrences are in the primary topic
        (primary = most frequent topic; ties broken by sorted VALID_TOPICS order)

    Returns list of {term, lang, context_topic, total_count, purity} dicts.
    """
    candidates = []
    for (entity, lang), topic_counts in entity_topic_counts.items():
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
            'lang': lang,
            'context_topic': primary_topic,
            'total_count': total,
            'purity': round(purity, 3),
        })

    logger.info(f"Step 3: {len(candidates)} candidates passed freq/purity filters (min_freq={min_freq})")

    # 2026-04-14: noise-keyword filter. Apply is_valid_keyword() to every
    # candidate before returning; junk terms like "BOTH", "II", "CL" get
    # dropped here instead of wasting LLM confirmation cost downstream.
    # See is_valid_keyword() docstring for the rule list.
    pre_filter_count = len(candidates)
    candidates = [
        c for c in candidates
        if is_valid_keyword(c['term'], c['total_count'], lang=c['lang'])
    ]
    dropped = pre_filter_count - len(candidates)
    if dropped:
        logger.info(
            f"Step 3: noise filter dropped {dropped} candidates "
            f"({len(candidates)} remain)"
        )

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


def _llm_confirm_chunk(candidates: list[dict]) -> list[dict]:
    """Send one batch of candidates to Claude for topic confirmation."""
    term_lines = json.dumps(
        [{'term': c['term'], 'lang': c.get('lang', 'en'), 'context_topic': c['context_topic']} for c in candidates],
        ensure_ascii=False,
        indent=2,
    )

    prompt = f"""The following terms appear frequently in news articles that were classified by topic.
For each term, confirm the best topic label from this fixed set:
world, politics, business, technology, ai, science, society, sports, entertainment

Terms (with the lang_group they came from and the topic they appeared under most often):
{term_lines}

Return ONLY a JSON array with one entry per term, in the same order.
Each entry: {{"term": "...", "confirmed_topic": "..."}}
Do not include confidence scores or any other fields.
Return exactly {len(candidates)} entries."""

    try:
        result = subprocess.run(
            [
                'claude', '-p',
                '--model', 'sonnet',
                '--system-prompt',
                'You are a topic-label confirmer. Output ONLY a JSON array '
                'of {"term": "...", "confirmed_topic": "..."} objects. No '
                'prose, no markdown, no explanation.',
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


def llm_confirm_candidates(candidates: list[dict]) -> list[dict]:
    """
    Send candidates to Claude for topic confirmation.
    Splits into chunks if len(candidates) > harvest_max_batch_size.
    """
    if not candidates:
        return []

    max_batch = int(SystemSettings.get_setting('harvest_max_batch_size', default=50))
    if len(candidates) > max_batch:
        all_confirmed: list[dict] = []
        for i in range(0, len(candidates), max_batch):
            chunk = candidates[i:i + max_batch]
            all_confirmed.extend(_llm_confirm_chunk(chunk))
        return all_confirmed
    return _llm_confirm_chunk(candidates)


# ---------------------------------------------------------------------------
# Step 5 — Load existing keywords + merge + expire + write
# ---------------------------------------------------------------------------

def load_existing_keywords() -> dict[str, dict[tuple[str, str], dict]]:
    """
    Load auto_keywords.json and return existing keyword state as:
      {topic: {(term, lang): {"last_seen": "YYYY-MM-DD", "score": N}}}

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
        result: dict[str, dict[tuple[str, str], dict]] = {}
        for topic, entries in raw.items():
            topic_kws: dict[tuple[str, str], dict] = {}
            has_legacy = False
            for entry in entries:
                if isinstance(entry, str):
                    lang = 'en' if entry.isascii() else 'zh'
                    topic_kws[(entry, lang)] = {'last_seen': today, 'score': 1}
                    has_legacy = True
                elif isinstance(entry, dict):
                    term = entry.get('term')
                    if term and isinstance(term, str):
                        if 'lang' not in entry:
                            has_legacy = True
                        lang = entry.get('lang') or ('en' if term.isascii() else 'zh')
                        topic_kws[(term, lang)] = {
                            'last_seen': entry.get('last_seen', today),
                            'score': int(entry.get('score', 1)),
                        }
            if has_legacy:
                logger.info(
                    f"auto_keywords.json: migrating legacy entries in topic '{topic}' "
                    f"(pre-2026-04-15 schema, lang defaults applied)"
                )
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
        key = (c['term'], c.get('lang', 'en'))
        new_count = c['total_count']

        if topic not in existing:
            existing[topic] = {}

        if key in existing[topic]:
            existing[topic][key]['score'] += new_count
            existing[topic][key]['last_seen'] = today
            updated += 1
        else:
            existing[topic][key] = {'last_seen': today, 'score': new_count}
            added += 1

    logger.info(f"Step 5 merge: {added} new keywords added, {updated} existing updated")

    # Apply expiry — two-tier based on score
    from datetime import date
    today_date = date.today()
    expired = 0
    for topic in list(existing.keys()):
        surviving = {}
        for key, kw in existing[topic].items():
            try:
                last_seen_date = date.fromisoformat(kw['last_seen'])
            except (ValueError, KeyError):
                last_seen_date = today_date  # malformed → treat as seen today
            silence_days = (today_date - last_seen_date).days
            threshold = strong_expire_days if kw['score'] >= strong_threshold else expire_days
            if silence_days <= threshold:
                surviving[key] = kw
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
                {'term': term, 'lang': lang, 'last_seen': kw['last_seen'], 'score': kw['score']}
                for (term, lang), kw in sorted(kws.items())
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
    import argparse

    parser = argparse.ArgumentParser(description='Keyword harvest worker')
    parser.add_argument('--once', action='store_true', help='Run once and exit (default behaviour)')
    parser.add_argument('--shadow', action='store_true',
                        help='Write to auto_keywords.shadow.json instead of live file')
    parser.add_argument('--dry-run-lang', metavar='LANG',
                        help='Run pipeline for a single lang_group and print what would be written, without persisting')
    args = parser.parse_args()

    if args.shadow:
        AUTO_KEYWORDS_PATH = AUTO_KEYWORDS_PATH.parent / 'auto_keywords.shadow.json'
        logger.info(f"Shadow mode: writing to {AUTO_KEYWORDS_PATH}")

    if args.dry_run_lang:
        lang = args.dry_run_lang
        logger.info(f"=== Dry-run for lang={lang} ===")
        cutoff_end = timezone.now()
        cutoff_start = cutoff_end - timedelta(hours=24)
        items = get_llm_classified_items(cutoff_start, cutoff_end)
        items = [i for i in items if (i.lang_group or 'en') == lang]
        logger.info(f"Filtered to {len(items)} items for lang={lang}")
        if items:
            entity_topic_counts = extract_entities_from_items(items)
            min_freq = int(SystemSettings.get_setting('harvest_min_freq', default=1))
            candidates = apply_filters(entity_topic_counts, min_freq=min_freq)
            logger.info(f"Would confirm {len(candidates)} candidates (skipping LLM in dry-run):")
            for c in candidates:
                logger.info(f"  {c}")
    else:
        run_harvest()
