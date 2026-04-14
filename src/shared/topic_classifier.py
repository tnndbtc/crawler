"""
Content-based topic classifier — heuristic pass.

Classifies TrendItems by topic using signals from bucket, platform,
surface key, lang_group, and title/description keywords.

Single-label: returns one story_category string and the signal that produced it.

Canonical vocabulary (9 values):
  world, politics, business, technology, ai, science, society, sports, entertainment

Returns (None, None) when unclassifiable — those items are passed to the LLM pass.

Hot-reload: call reload_keywords_if_updated() each worker cycle to pick up
new auto_keywords.json without restarting the process. Returns True if
patterns were reloaded (caller should trigger version re-queue).
"""

import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Path to the auto-generated keyword file produced by keyword_harvest_worker.py
_AUTO_KEYWORDS_PATH = Path(__file__).parent.parent.parent / 'config' / 'auto_keywords.json'

# Last-seen mtime of auto_keywords.json — used for hot-reload detection
_AUTO_KEYWORDS_MTIME: float = 0.0

# Canonical 9-value vocabulary
VALID_CATEGORIES: frozenset[str] = frozenset({
    'world', 'politics', 'business', 'technology', 'ai',
    'science', 'society', 'sports', 'entertainment',
})

# Keep VALID_TOPICS as alias for backward compatibility (keyword_harvest_worker imports it)
VALID_TOPICS = VALID_CATEGORIES

# Within-category priority: highest wins when multiple keyword matches
CATEGORY_PRIORITY: list[str] = [
    'ai', 'politics', 'business', 'technology', 'science',
    'world', 'society', 'sports', 'entertainment',
]

# ---------------------------------------------------------------------------
# Signal 1: Bucket override (HIGHEST confidence)
# ---------------------------------------------------------------------------

BUCKET_STORY_CATEGORY_MAP: dict[str, str] = {
    'category_ai':            'ai',
    'category_tech':          'technology',
    'category_finance':       'business',
    'category_politics':      'politics',
    'category_entertainment': 'entertainment',
    'category_gaming':        'entertainment',
    'category_science':       'science',
    'category_sports':        'sports',
    'category_lifestyle':     'society',
    'category_health':        'society',
    'category_crime':         'society',
    'category_environment':   'science',
    'international_news':     'world',
    'world_news':             'world',
    'global_news':            'world',
}


def _classify_by_bucket(bucket: str) -> str | None:
    """Signal 1: bucket directly encodes a story category."""
    return BUCKET_STORY_CATEGORY_MAP.get(bucket)


# ---------------------------------------------------------------------------
# Signal 2: Platform heuristic (HIGH confidence)
# ---------------------------------------------------------------------------

PLATFORM_STORY_CATEGORY_MAP: dict[str, str] = {
    # Kept: literally single-topic feeds (100% of content is the mapped category).
    # Removed 2026-04-14: github, hackernews, v2ex, producthunt — these platforms
    # carry significant non-technology content (politics, culture, business, etc.)
    # and were blind source-level tagging. Items from those platforms now flow
    # to keyword/LLM (Haiku) for per-post classification.
    'paperswithcode': 'ai',
    'arxiv_ai_rss':   'ai',
    'devto':          'technology',
    'lobsters':       'technology',
    'stackoverflow':  'technology',
}


def _classify_by_platform(platform: str) -> str | None:
    """Signal 2: platform identity implies story category."""
    return PLATFORM_STORY_CATEGORY_MAP.get(platform)


# ---------------------------------------------------------------------------
# Signal 3: Keyword matching on title + description (MEDIUM confidence)
# Single-label: highest-priority matching category returned.
# ---------------------------------------------------------------------------

KEYWORD_MAP: dict[str, list[str]] = {
    'politics': [
        'election', 'elections', 'elected', 'president', 'congress', 'senate',
        'parliament', 'sanctions', 'treaty', 'military', 'NATO',
        'G7', 'G20', 'United Nations', 'Xi Jinping', 'Putin',
        'Trump', 'Biden',
        # Was bare 'Modi' — matched Spanish 'modi-' prefix (modificar,
        # modificación). Contextualized to avoid false positives.
        ' Modi ', 'PM Modi', 'Narendra Modi',
        'Macron', 'chancellor', 'prime minister',
        'tariff', 'tariffs', 'diplomat', 'diplomacy', 'geopolit',
        # War / conflict — compound forms only. Bare ' war ' was removed
        # because it matched German "war" (= "was") and ' UN ' was removed
        # because it matched Spanish/French/Italian/Portuguese "un"
        # (indefinite article) and English "sun"/"run"/"nun"/"fun" + space.
        'war with', 'at war', 'war crime', 'war crimes',
        'war zone', 'war veteran', 'war veterans',
        'Iran', 'Israel', 'Gaza', 'Lebanon', 'Palestine', 'Ukraine',
        'airstrike', 'airstrikes', 'ceasefire', 'cease-fire',
        'troops', 'soldiers', 'Pentagon', 'warzone', 'occupation',
        'Hezbollah', 'Hamas', 'Kremlin', 'Zelensky', 'Netanyahu',
        'Border Patrol', 'deport', 'deportation', 'deportee',
        'asylum seeker', 'asylum seekers', 'undocumented immigrant',
        'Taiwan strait', 'Taiwan Strait', '台海', 'South China Sea', '南海',
        'one country two systems',
        # Was 'NPC ' (trailing space only) — matched Nigerian NNPC oil co.
        # Original intent was Chinese National People's Congress.
        ' NPC ', "National People's Congress",
        # Was 'Belt and Road' + 'BRI ' — BRI was a noisy 3-letter acronym.
        # Consolidated to the full name.
        'Belt and Road Initiative',
        'Politburo',
        # Added full name alongside existing acronym for clarity.
        "People's Liberation Army", ' PLA ',
        # Was bare 'Hong Kong' — contextualized to specific political events
        # (was bleeding into business "HK stock market" and entertainment
        # "HK film festival").
        'Hong Kong national security law', 'Hong Kong legco',
        'Hong Kong extradition bill',
        'national security law',
        'extradition', 'reunification', 'cross-strait',
        '政治', '选举', '制裁', '外交',
    ],
    'business': [
        'Fed ', 'Federal Reserve', 'rate hike', 'rate cut', 'inflation',
        ' GDP', 'recession', 'earnings', ' IPO', ' stock ', 'stocks',
        ' bond', ' yield', 'crypto', 'bitcoin', ' ETF', 'hedge fund',
        'IMF', ' ECB', 'deficit', 'debt ceiling', 'stock market',
        'S&P', 'Nasdaq', 'Dow ', 'Wall Street', 'interest rate',
        'PBOC', "People's Bank of China", ' RMB', ' yuan', 'renminbi',
        'A-shares', 'CSI 300', 'Hang Seng', 'property crisis', 'Evergrande',
        'China tariff', 'trade war', 'decoupling', 'supply chain China',
        'RCEP', 'AIIB',
        '美联储', '通胀', '经济', '股市', '加息', '降息',
        '株価', '投資', '金融',
        'merger', 'acquisition', 'layoff', 'layoffs', 'CEO', 'revenue',
        'quarterly results', 'supply chain', 'logistics', 'retail sales',
        'brand', 'bankruptcy', 'valuation', 'venture capital', 'funding round',
        'Alibaba', 'Tencent', 'JD.com', 'Pinduoduo', 'Meituan', 'Xiaomi',
        'Ant Group', 'DiDi', 'Chinese EV', 'lithium supply chain',
        'ビジネス', '経営', '企業',
    ],
    'ai': [
        'LLM', ' GPT', ' AI ', ' AI,', ' AI.',
        'artificial intelligence', 'machine learning', 'deep learning',
        'neural network', 'neural net', 'transformer model', 'benchmark', 'AGI',
        'Anthropic', 'OpenAI', 'DeepMind', 'Mistral',
        'AI Act', 'large language model', 'foundation model',
        'diffusion model', 'generative AI', 'AI model', 'AI system',
        # Model names — compound forms only. Bare 'Claude' matched the
        # French given name and Claude Monet; bare 'Gemini' matched the
        # zodiac sign; bare 'Llama' matched the animal; bare 'Grok'
        # matched the dictionary verb.
        'Claude AI', 'Claude Sonnet', 'Claude Opus', 'Claude Haiku',
        'Anthropic Claude', 'Gemini Pro', 'Gemini model', 'Google Gemini',
        'Meta Llama', 'Llama 3', 'Llama model', 'Grok xAI', 'xAI Grok',
        'DeepSeek', 'Kimi ', 'Ernie Bot', 'Wenxin', 'PanGu',
        'AI regulation China', 'Chinese AI',
        'robotics', 'autonomous vehicle', 'autonomous vehicles',
        'autonomous robot', 'autonomous system', 'autonomous systems',
        'computer vision', 'natural language processing', 'NLP model',
        'model training', 'model inference', 'AI inference',
        'AI agent', 'AI agents',
        'AI技術', '人工知能',
    ],
    'technology': [
        # Brand names — compound forms only. Bare 'Apple' matched
        # "Apple pie"; bare 'Amazon' matched "Amazon rainforest"; bare
        # 'Meta' matched "meta-analysis" of vaccines (science).
        'Apple iPhone', 'Apple Watch', 'Apple Silicon', 'Apple AI',
        'Amazon AWS', 'Amazon Prime', 'Amazon Alexa', 'Amazon EC2',
        'Tesla Model', 'Tesla Autopilot', 'Tesla Cybertruck',
        'Google Search', 'Google Cloud', 'Google DeepMind',
        'Microsoft Azure', 'Microsoft Copilot', 'Microsoft 365',
        'Meta Quest', 'Meta AI', 'Meta Reality Labs',
        'startup', 'software', 'hardware', ' chip', 'semiconductor',
        'iPhone', 'Android', 'open source', 'developer', 'programming',
        'cloud computing', 'cybersecurity', 'data breach', 'quantum computing',
        'Huawei', 'Kirin chip', 'HarmonyOS', 'SMIC', 'TSMC',
        'ByteDance', 'TikTok', 'Douyin', ' DJI', ' BYD', 'CATL',
        'chip sanctions', 'export controls', 'chip ban',
        'テクノロジー', 'スマートフォン', '技術革新',
    ],
    'science': [
        'researchers', 'scientists', 'research paper', 'new study',
        'discovered', 'published in', 'Nature ', 'Science journal',
        'genome', 'quantum', 'CERN', 'telescope', 'NASA ', 'SpaceX',
        'climate change', 'carbon emissions', 'new species', 'biology',
        'physics', 'chemistry', 'experiment',
        'climate', 'carbon', 'emissions', 'renewable energy', 'solar',
        'wildfire', ' flood', 'drought', 'deforestation', ' COP ',
        'Paris Agreement', 'net zero', 'biodiversity', 'pollution',
        '科学', '研究', '宇宙',
    ],
    'society': [
        'immigration', 'migrants', 'refugee', 'refugees', 'protest', 'protesters',
        'strike', 'labor union', 'inequality', 'poverty', 'education', 'university',
        'demographic', 'religion', 'culture war', 'social media',
        'family separation', 'human rights',
        'vaccine', 'vaccination', 'virus', 'pandemic', 'outbreak',
        ' FDA', ' WHO', 'cancer', ' drug ', 'hospital', 'patient',
        'treatment', 'mental health', 'obesity', 'diabetes', 'COVID',
        'disease', 'epidemic', 'public health',
        'arrested', 'charged with', 'murder', 'shooting', 'attack',
        'police', 'court ruling', 'trial', 'sentenced', 'investigation',
        'fraud', 'corruption', 'indicted',
        '健康', '医療', '病院',
    ],
    'sports': [
        'NFL', 'NBA', 'FIFA', 'Premier League', 'Champions League',
        'Olympics', 'World Cup', 'tournament', 'championship',
        'transfer', 'match result', 'season opener', 'Grand Slam',
        'Formula 1', 'F1 ', 'Super Bowl', 'playoff',
        '野球', 'スポーツ', 'サッカー', 'バスケ',
    ],
    'entertainment': [
        ' movie', ' film', 'Netflix', 'Disney', 'box office', 'Oscar',
        'Grammy', 'music album', 'singer', ' actor', 'celebrity',
        'Taylor Swift', 'BTS', 'K-pop', 'anime', 'video game',
        'PlayStation', 'Xbox', 'Nintendo', 'streaming',
        'アニメ', '映画', 'ゲーム', 'ドラマ', '音楽',
    ],
    # 'world' is NOT produced by keyword match — too ambiguous without explicit
    # editorial bucket/surface configuration.
}

# ---------------------------------------------------------------------------
# Merge static KEYWORD_MAP with auto_keywords.json at module load.
# ---------------------------------------------------------------------------

def _load_auto_keywords() -> dict[str, list[str]]:
    """
    Load auto_keywords.json and return {category: [term, ...]} for pattern building.
    Keys must be story_category values (9-value vocab).
    """
    try:
        with open(_AUTO_KEYWORDS_PATH) as f:
            data = json.load(f)
        raw = data.get('keywords', {})
        result: dict[str, list[str]] = {}
        for category, entries in raw.items():
            if category not in VALID_CATEGORIES:
                continue  # skip old-vocabulary keys (tech, finance, etc.)
            terms = []
            for entry in entries:
                if isinstance(entry, str):
                    terms.append(entry)
                elif isinstance(entry, dict):
                    term = entry.get('term')
                    if term and isinstance(term, str):
                        terms.append(term)
            result[category] = terms
        return result
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"auto_keywords.json unreadable ({e}); using static keywords only")
        return {}


def _build_keyword_patterns() -> dict[str, list[re.Pattern]]:
    auto_kw = _load_auto_keywords()
    patterns: dict[str, list[re.Pattern]] = {}
    for category, static_keywords in KEYWORD_MAP.items():
        auto_keywords = auto_kw.get(category, [])
        merged = list(dict.fromkeys(static_keywords + auto_keywords))
        patterns[category] = [re.compile(re.escape(kw), re.IGNORECASE) for kw in merged]
    for category, auto_keywords in auto_kw.items():
        if category not in patterns:
            patterns[category] = [re.compile(re.escape(kw), re.IGNORECASE) for kw in auto_keywords]
    return patterns


_KEYWORD_PATTERNS: dict[str, list[re.Pattern]] = _build_keyword_patterns()


def reload_keywords_if_updated() -> bool:
    """
    Check if auto_keywords.json has changed since last load.
    If so, rebuild _KEYWORD_PATTERNS in-place and return True.
    Returns False if nothing changed or the file is absent.

    Call this once per worker cycle. When True is returned, the caller should
    trigger the version re-queue so heuristic gets a second chance with new keywords.
    """
    global _KEYWORD_PATTERNS, _AUTO_KEYWORDS_MTIME
    try:
        mtime = _AUTO_KEYWORDS_PATH.stat().st_mtime
    except FileNotFoundError:
        return False

    if mtime <= _AUTO_KEYWORDS_MTIME:
        return False

    _KEYWORD_PATTERNS = _build_keyword_patterns()
    _AUTO_KEYWORDS_MTIME = mtime
    logger.info(
        f"auto_keywords.json updated (mtime={mtime:.0f}) — "
        f"keyword patterns reloaded across {len(_KEYWORD_PATTERNS)} categories"
    )
    return True


def _classify_by_keywords(
    title: str,
    description: str | None = None,
    auto_keywords_override: dict | None = None,
) -> str | None:
    """
    Signal 3: keyword matching on title and optional description.

    Description is capped at 300 chars. Returns the highest-priority matching
    story_category, or None if no match.

    auto_keywords_override: when provided, merged with KEYWORD_MAP to build
    temporary patterns for this call instead of using the global _KEYWORD_PATTERNS.
    """
    text = (title or '').strip()
    if description:
        text = text + ' ' + description[:300]
    if not text:
        return None

    if auto_keywords_override is not None:
        # Build temporary patterns: static keywords + caller-supplied auto keywords
        patterns: dict[str, list[re.Pattern]] = {}
        for category, static_kws in KEYWORD_MAP.items():
            auto_kws = auto_keywords_override.get(category, [])
            merged = list(dict.fromkeys(static_kws + auto_kws))
            patterns[category] = [re.compile(re.escape(kw), re.IGNORECASE) for kw in merged]
        for category, auto_kws in auto_keywords_override.items():
            if category not in patterns:
                patterns[category] = [re.compile(re.escape(kw), re.IGNORECASE) for kw in auto_kws]
    else:
        patterns = _KEYWORD_PATTERNS

    matched: set[str] = set()
    for category, pats in patterns.items():
        for pattern in pats:
            if pattern.search(text):
                matched.add(category)
                break

    if not matched:
        return None

    for cat in CATEGORY_PRIORITY:
        if cat in matched:
            return cat
    return None


# ---------------------------------------------------------------------------
# Signal 4: Locale + platform fallback (LOW confidence)
# ---------------------------------------------------------------------------

def _classify_by_locale_fallback(lang_group: str | None, platform: str) -> str | None:
    """Signal 4: conservative fallback based on locale + platform combo."""
    if lang_group == 'zh' and platform == 'bilibili':
        return 'entertainment'
    return None


# ---------------------------------------------------------------------------
# Signal 5: Surface key fallback (LOW confidence)
# ---------------------------------------------------------------------------

SURFACE_STORY_CATEGORY_MAP: dict[str, str] = {
    # Emptied 2026-04-14 as part of the "no blind source-level tagging"
    # fix. This map previously contained 93 entries assigning a single
    # category to entire source feeds. Even "topic-specific" sources
    # like reddit_worldnews were measured at 82.9% political (17% of
    # content was mis-tagged); general news outlets (BBC, Reuters,
    # Le Monde, NHK, Google News editions, national RSS feeds) were
    # worse.
    #
    # Since the LLM layer now uses Haiku (~60x cheaper than larger
    # models), per-post classification via keyword + LLM is cheap
    # enough to be the default. Blind source-level tagging is no
    # longer justified on cost grounds and was never justified on
    # accuracy grounds.
    #
    # Signal 5 (surface fallback) is now a no-op. Items previously
    # classified here flow through Signal 3 (keyword) and, if no
    # keyword matches, Signal 6 (LLM / Haiku) for per-post
    # classification.
    #
    # Historical items that were blind-tagged here are removed by a
    # one-off SQL DELETE as part of the rollout.
}


def _classify_by_surface(surface_key: str) -> str | None:
    """Signal 5: surface key fallback — fires only when all other signals fail."""
    return SURFACE_STORY_CATEGORY_MAP.get(surface_key)


# ---------------------------------------------------------------------------
# Main classification function
# ---------------------------------------------------------------------------

def classify_item(
    bucket: str,
    platform: str,
    surface_key: str,
    lang_group: str | None,
    title: str,
    description: str | None = None,
    auto_keywords: dict | None = None,
) -> tuple[str | None, str | None]:
    """
    Classify a TrendItem and return (story_category, classified_by_signal).

    story_category: one of the 9 canonical values, or None if unclassifiable.
    classified_by_signal: which signal produced the result:
        'bucket' | 'platform' | 'keyword' | 'locale' | 'surface' | None

    Signal priority (first hit wins):
      1. Bucket override  — highest confidence
      2. Platform         — high confidence
      3. Keyword matching — medium confidence (title + description[:300])
      4. Locale fallback  — low confidence
      5. Surface fallback — low confidence
      6. None             — unclassifiable, pass to LLM
    """
    # Signal 1: bucket
    cat = _classify_by_bucket(bucket)
    if cat:
        return cat, 'bucket'

    # Signal 2: platform
    cat = _classify_by_platform(platform)
    if cat:
        return cat, 'platform'

    # Signal 3: keywords
    cat = _classify_by_keywords(title, description, auto_keywords)
    if cat:
        return cat, 'keyword'

    # Signal 4: locale fallback
    cat = _classify_by_locale_fallback(lang_group, platform)
    if cat:
        return cat, 'locale'

    # Signal 5: surface fallback
    cat = _classify_by_surface(surface_key)
    if cat:
        return cat, 'surface'

    return None, None


# ---------------------------------------------------------------------------
# Backward-compatibility shim
# ---------------------------------------------------------------------------

def classify_topic_tags(
    bucket: str,
    platform: str,
    surface_key: str,
    lang_group: str | None,
    title: str,
    description: str | None = None,
) -> list[str]:
    """
    Deprecated shim for old callers. Returns a list with the single classified
    story_category, or [] if unclassifiable.

    New code should call classify_item() directly.
    """
    cat, _ = classify_item(bucket, platform, surface_key, lang_group, title, description)
    return [cat] if cat else []
