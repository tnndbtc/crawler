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
    'paperswithcode': 'ai',
    'arxiv_ai_rss':   'ai',
    'github':         'technology',
    'hackernews':     'technology',
    'devto':          'technology',
    'lobsters':       'technology',
    'stackoverflow':  'technology',
    'v2ex':           'technology',
    'producthunt':    'technology',
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
        'parliament', 'sanctions', 'treaty', 'war ', 'military', 'NATO',
        'G7', 'G20', 'UN ', 'United Nations', 'Xi Jinping', 'Putin',
        'Trump', 'Biden', 'Modi', 'Macron', 'chancellor', 'prime minister',
        'tariff', 'tariffs', 'diplomat', 'diplomacy', 'geopolit',
        'Iran', 'Israel', 'Gaza', 'Lebanon', 'Palestine', 'Ukraine',
        'airstrike', 'airstrikes', 'ceasefire', 'cease-fire',
        'troops', 'soldiers', 'Pentagon', 'warzone', 'occupation',
        'Hezbollah', 'Hamas', 'Kremlin', 'Zelensky', 'Netanyahu',
        'Border Patrol', 'deport', 'deportation', 'deportee',
        'asylum seeker', 'asylum seekers', 'undocumented immigrant',
        'Taiwan strait', 'Taiwan Strait', '台海', 'South China Sea', '南海',
        'one country two systems', 'NPC ', 'Belt and Road', 'BRI ',
        'Politburo', ' PLA ', 'Hong Kong', 'national security law',
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
        'LLM', ' GPT', 'Claude', 'Gemini', 'Llama', ' AI ', ' AI,', ' AI.',
        'artificial intelligence', 'machine learning', 'deep learning',
        'neural network', 'neural net', 'transformer model', 'benchmark', 'AGI',
        'Anthropic', 'OpenAI', 'DeepMind', 'Mistral', 'xAI', 'Grok',
        'AI Act', 'large language model', 'foundation model',
        'diffusion model', 'generative AI', 'AI model', 'AI system',
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
        'Apple', 'Google', 'Microsoft', 'Meta', 'Amazon', 'Tesla',
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
    # Reddit — topic-specific subreddits (political discussion communities → politics)
    'reddit_worldnews':      'politics',
    'reddit_news':           'politics',
    'reddit_economics':      'business',
    'reddit_geopolitics':    'politics',
    'reddit_ukraine':        'politics',
    # Reddit — regional subreddits (community discussion, not editorial news)
    'reddit_europe':         'politics',
    'reddit_unitedkingdom':  'politics',
    'reddit_france':         'politics',
    'reddit_de':             'politics',
    'reddit_arabs':          'politics',
    'reddit_turkey':         'politics',
    'reddit_africa':         'politics',
    'reddit_india':          'politics',
    'reddit_philippines':    'politics',
    'reddit_askARussian':    'politics',
    'reddit_poland':         'politics',
    'reddit_brasil':         'society',
    'reddit_argentina':      'society',
    'reddit_mexico':         'society',
    'reddit_italy':          'society',
    'reddit_sweden':         'society',
    'reddit_es':             'society',
    'reddit_australia':      'society',
    'reddit_canada':         'society',
    'reddit_malaysia':       'society',
    # News RSS feeds — general international/national outlets → world
    'g1_rss':                'world',
    'aajtak_rss':            'world',
    'cumhuriyet_rss':        'world',
    'folha_rss':             'world',
    'aljazeera_ar_rss':      'world',
    'onet_rss':              'world',
    'tass_rss':              'politics',   # Russian state media — explicitly political
    'spiegel_rss':           'world',
    'nhk_news_rss':          'world',
    'tvn24_rss':             'world',
    'ansa_rss':              'world',
    'meduza_rss':            'politics',   # Russian opposition — heavy political focus
    'france24_ar_rss':       'world',
    'zeit_rss':              'world',
    'bbc_hindi_rss':         'world',
    'bbcarabic_rss':         'world',
    'elpais_rss':            'world',
    'lemonde_rss':           'world',
    'dw_ru_rss':             'world',
    'dw_ar_rss':             'world',
    'rfi_rss':               'world',
    '36kr_rss':              'business',
    # Generic RSS — general country news portals → world
    'portugal_news_rss':     'world',
    'vnexpress_rss':         'world',
    'sweden_news_rss':       'world',
    'india_english_news_rss':'world',
    'globo_g1_rss':          'world',
    'argentina_news_rss':    'world',
    'nunl_rss':              'world',
    'thailand_news_rss':     'world',
    'philippines_news_rss':  'world',
    # Dedicated news platforms — major international outlets → world
    'bbc_news':              'world',
    'guardian_news':         'world',
    'reuters_news':          'world',
    'aljazeera_news':        'world',
    # Google News editions — general aggregators → world
    'google_news':           'world',
    'google_news_de':        'world',
    'google_news_br':        'world',
    'google_news_fr':        'world',
    'google_news_es':        'world',
    'google_news_it':        'world',
    'google_news_pt':        'world',
    'google_news_kr':        'world',
    'google_news_gb':        'world',
    'google_news_jp':        'world',
    'google_news_ua':        'world',
    'google_news_in_en':     'world',
    'google_news_ca':        'world',
    'google_news_pk':        'world',
    'google_news_mx':        'world',
    'google_news_au':        'world',
    'google_news_ng':        'world',
    'allafrica_news':        'world',
    'thehindu_news':         'world',
    'almonitor_news':        'world',
    'bloomberg_news':        'business',
    # Social / trending platforms
    'weibo_hot':             'society',
    'baidu_hot':             'society',
    'naver_news_ranking':    'world',
    'nicovideo_ranking':     'entertainment',
    'hatena_hotentry':       'technology',
    'wenxuecity_news':       'society',
    'wikipedia_most_read':   'society',
    'sina_news':             'politics',   # Chinese state-controlled media — explicitly political
    'chinatimes_news':       'world',
    'nyt_news':              'world',
    'hk01_news':             'world',
    'mirrormedia_news':      'world',
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
