"""
Content-based topic classifier — heuristic pass.

Classifies TrendItems by topic using signals from bucket, platform,
surface key, lang_group, and title/description keywords.

Multi-label: an article can have multiple topic tags.

Fixed vocabulary (12 labels):
  politics, finance, ai, tech, science, entertainment,
  sports, business, crime, society, health, environment

Returns [] when unclassifiable — those items are passed to the LLM pass.

Hot-reload: call reload_keywords_if_updated() each worker cycle to pick up
new auto_keywords.json without restarting the process. Returns True if
patterns were reloaded (caller should reset "Tried, no tags" items).
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

VALID_TOPICS: frozenset[str] = frozenset({
    'politics', 'finance', 'ai', 'tech', 'science',
    'entertainment', 'sports', 'business', 'crime',
    'society', 'health', 'environment',
})

# ---------------------------------------------------------------------------
# Signal 1: Bucket override (HIGHEST confidence)
# Buckets are set at crawl time from TrendSurface.bucket; they already encode
# an editorial category, so we trust them unconditionally.
# ---------------------------------------------------------------------------

BUCKET_TOPIC_MAP: dict[str, list[str]] = {
    'category_tech':          ['tech'],
    'category_finance':       ['finance'],
    'category_politics':      ['politics'],
    'category_entertainment': ['entertainment'],
    'category_gaming':        ['entertainment'],
    'category_science':       ['science'],
    'category_sports':        ['sports'],
    'category_lifestyle':     ['society'],
}


def _classify_by_bucket(bucket: str) -> list[str]:
    """Signal 1: bucket directly encodes a topic category."""
    return BUCKET_TOPIC_MAP.get(bucket, [])


# ---------------------------------------------------------------------------
# Signal 2: Platform heuristic (HIGH confidence)
# Certain platforms publish almost exclusively in one topic domain.
# ---------------------------------------------------------------------------

PLATFORM_TOPIC_MAP: dict[str, list[str]] = {
    'paperswithcode': ['ai', 'tech'],
    'arxiv_ai_rss':   ['ai', 'tech'],
    'github':         ['tech'],
    'hackernews':     ['tech'],    # default; keywords may add more labels
    'devto':          ['tech'],
    'lobsters':       ['tech'],
    'stackoverflow':  ['tech'],
    'v2ex':           ['tech'],
    'producthunt':    ['tech'],
}


def _classify_by_platform(platform: str) -> list[str]:
    """Signal 2: platform identity implies topic."""
    return PLATFORM_TOPIC_MAP.get(platform, [])


# ---------------------------------------------------------------------------
# Signal 3: Keyword matching on title + description (MEDIUM confidence)
# Multi-label: all matching topics are returned.
# ---------------------------------------------------------------------------

KEYWORD_MAP: dict[str, list[str]] = {
    'politics': [
        'election', 'elections', 'elected', 'president', 'congress', 'senate',
        'parliament', 'sanctions', 'treaty', 'war ', 'military', 'NATO',
        'G7', 'G20', 'UN ', 'United Nations', 'Xi Jinping', 'Putin',
        'Trump', 'Biden', 'Modi', 'Macron', 'chancellor', 'prime minister',
        'tariff', 'tariffs', 'diplomat', 'diplomacy', 'geopolit',
        # Conflict and military
        'Iran', 'Israel', 'Gaza', 'Lebanon', 'Palestine', 'Ukraine',
        'airstrike', 'airstrikes', 'ceasefire', 'cease-fire',
        'troops', 'soldiers', 'Pentagon', 'warzone', 'occupation',
        'Hezbollah', 'Hamas', 'Kremlin', 'Zelensky', 'Netanyahu',
        # Immigration / border
        'Border Patrol', 'deport', 'deportation', 'deportee',
        'asylum seeker', 'asylum seekers', 'undocumented immigrant',
        # China-global politics
        'Taiwan strait', 'Taiwan Strait', '台海', 'South China Sea', '南海',
        'one country two systems', 'NPC ', 'Belt and Road', 'BRI ',
        'Politburo', ' PLA ', 'Hong Kong', 'national security law',
        'extradition', 'reunification', 'cross-strait',
        '政治', '选举', '制裁', '外交',
    ],
    'finance': [
        'Fed ', 'Federal Reserve', 'rate hike', 'rate cut', 'inflation',
        ' GDP', 'recession', 'earnings', ' IPO', ' stock ', 'stocks',
        ' bond', ' yield', 'crypto', 'bitcoin', ' ETF', 'hedge fund',
        'IMF', ' ECB', 'deficit', 'debt ceiling', 'stock market',
        'S&P', 'Nasdaq', 'Dow ', 'Wall Street', 'interest rate',
        # China-global finance
        'PBOC', 'People\'s Bank of China', ' RMB', ' yuan', 'renminbi',
        'A-shares', 'CSI 300', 'Hang Seng', 'property crisis', 'Evergrande',
        'China tariff', 'trade war', 'decoupling', 'supply chain China',
        'RCEP', 'AIIB',
        '美联储', '通胀', '经济', '股市', '加息', '降息',
        # Japanese
        '株価', '投資', '金融',
    ],
    'ai': [
        'LLM', ' GPT', 'Claude', 'Gemini', 'Llama', ' AI ', ' AI,', ' AI.',
        'artificial intelligence', 'machine learning', 'deep learning',
        'neural network', 'transformer model', 'benchmark', 'AGI',
        'Anthropic', 'OpenAI', 'DeepMind', 'Mistral', 'xAI', 'Grok',
        'AI Act', 'large language model', 'foundation model',
        'diffusion model', 'generative AI', 'AI model', 'AI system',
        # China AI
        'DeepSeek', 'Kimi ', 'Ernie Bot', 'Wenxin', 'PanGu',
        'AI regulation China', 'Chinese AI',
        # Japanese
        'AI技術', '人工知能',
    ],
    'tech': [
        'Apple', 'Google', 'Microsoft', 'Meta', 'Amazon', 'Tesla',
        'startup', 'software', 'hardware', ' chip', 'semiconductor',
        'iPhone', 'Android', 'open source', 'developer', 'programming',
        'cloud computing', 'cybersecurity', 'data breach', 'quantum computing',
        # China tech
        'Huawei', 'Kirin chip', 'HarmonyOS', 'SMIC', 'TSMC',
        'ByteDance', 'TikTok', 'Douyin', ' DJI', ' BYD', 'CATL',
        'chip sanctions', 'export controls', 'chip ban',
        # Japanese
        'テクノロジー', 'スマートフォン', '技術革新',
    ],
    'science': [
        'researchers', 'scientists', 'research paper', 'new study',
        'discovered', 'published in', 'Nature ', 'Science journal',
        'genome', 'quantum', 'CERN', 'telescope', 'NASA ', 'SpaceX',
        'climate change', 'carbon emissions', 'new species', 'biology',
        'physics', 'chemistry', 'experiment',
        # Japanese
        '科学', '研究', '宇宙',
    ],
    'health': [
        'vaccine', 'vaccination', 'virus', 'pandemic', 'outbreak',
        ' FDA', ' WHO', 'cancer', ' drug ', 'hospital', 'patient',
        'treatment', 'mental health', 'obesity', 'diabetes', 'COVID',
        'disease', 'epidemic', 'public health',
        # Japanese
        '健康', '医療', '病院',
    ],
    'sports': [
        'NFL', 'NBA', 'FIFA', 'Premier League', 'Champions League',
        'Olympics', 'World Cup', 'tournament', 'championship',
        'transfer', 'match result', 'season opener', 'Grand Slam',
        'Formula 1', 'F1 ', 'Super Bowl', 'playoff',
        # Japanese
        '野球', 'スポーツ', 'サッカー', 'バスケ',
    ],
    'entertainment': [
        ' movie', ' film', 'Netflix', 'Disney', 'box office', 'Oscar',
        'Grammy', 'music album', 'singer', ' actor', 'celebrity',
        'Taylor Swift', 'BTS', 'K-pop', 'anime', 'video game',
        'PlayStation', 'Xbox', 'Nintendo', 'streaming',
        # Japanese
        'アニメ', '映画', 'ゲーム', 'ドラマ', '音楽',
    ],
    'business': [
        'merger', 'acquisition', 'layoff', 'layoffs', 'CEO', 'revenue',
        'quarterly results', 'supply chain', 'logistics', 'retail sales',
        'brand', 'bankruptcy', 'valuation', 'venture capital', 'funding round',
        # China business
        'Alibaba', 'Tencent', 'JD.com', 'Pinduoduo', 'Meituan', 'Xiaomi',
        'Ant Group', 'DiDi', 'Chinese EV', 'lithium supply chain',
        # Japanese
        'ビジネス', '経営', '企業',
    ],
    'crime': [
        'arrested', 'charged with', 'murder', 'shooting', 'attack',
        'police', 'court ruling', 'trial', 'sentenced', 'investigation',
        'fraud', 'corruption', 'indicted',
    ],
    'environment': [
        'climate', 'carbon', 'emissions', 'renewable energy', 'solar',
        'wildfire', ' flood', 'drought', 'deforestation', ' COP ',
        'Paris Agreement', 'net zero', 'biodiversity', 'pollution',
    ],
    'society': [
        'immigration', 'migrants', 'refugee', 'refugees', 'protest', 'protesters',
        'strike', 'labor union', 'inequality', 'poverty', 'education', 'university',
        'demographic', 'religion', 'culture war', 'social media',
        'family separation', 'human rights',
    ],
}

# ---------------------------------------------------------------------------
# Merge static KEYWORD_MAP with auto_keywords.json (if present) at module load.
# auto_keywords.json is generated weekly by keyword_harvest_worker.py.
# On first deploy (file absent) or corrupt write, falls back to static only.
# ---------------------------------------------------------------------------

def _load_auto_keywords() -> dict[str, list[str]]:
    """
    Load auto_keywords.json and return {topic: [term, ...]} for pattern building.

    Handles three entry formats (backwards compatible):
      - Plain string:            "Zelensky"          → term = "Zelensky"
      - Old object (no score):   {"term": "..."}     → term = entry["term"]
      - New object (with score): {"term": "...", "last_seen": "...", "score": N}
                                                     → term = entry["term"]
    """
    try:
        with open(_AUTO_KEYWORDS_PATH) as f:
            data = json.load(f)
        raw = data.get('keywords', {})
        result: dict[str, list[str]] = {}
        for topic, entries in raw.items():
            terms = []
            for entry in entries:
                if isinstance(entry, str):
                    terms.append(entry)
                elif isinstance(entry, dict):
                    term = entry.get('term')
                    if term and isinstance(term, str):
                        terms.append(term)
            result[topic] = terms
        return result
    except FileNotFoundError:
        return {}  # first deploy: file not yet generated, safe to skip
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"auto_keywords.json unreadable ({e}); using static keywords only")
        return {}


def _build_keyword_patterns() -> dict[str, list[re.Pattern]]:
    auto_kw = _load_auto_keywords()
    patterns: dict[str, list[re.Pattern]] = {}
    for topic, static_keywords in KEYWORD_MAP.items():
        auto_keywords = auto_kw.get(topic, [])
        # Static keywords first, auto appended; deduplicated, order preserved
        merged = list(dict.fromkeys(static_keywords + auto_keywords))
        patterns[topic] = [re.compile(re.escape(kw), re.IGNORECASE) for kw in merged]
    # Handle any topics in auto_kw not in static KEYWORD_MAP
    for topic, auto_keywords in auto_kw.items():
        if topic not in patterns:
            patterns[topic] = [re.compile(re.escape(kw), re.IGNORECASE) for kw in auto_keywords]
    return patterns


# Pre-compile case-insensitive patterns at module load time.
# Use reload_keywords_if_updated() to hot-reload without restarting.
_KEYWORD_PATTERNS: dict[str, list[re.Pattern]] = _build_keyword_patterns()


def reload_keywords_if_updated() -> bool:
    """
    Check if auto_keywords.json has changed since last load.
    If so, rebuild _KEYWORD_PATTERNS in-place and return True.
    Returns False if nothing changed or the file is absent.

    Call this once per worker cycle (e.g. in run_worker_loop before run_once).
    When True is returned, the caller should reset "Tried, no tags" items so
    the heuristic gets a second chance with the new keywords before LLM runs.
    """
    global _KEYWORD_PATTERNS, _AUTO_KEYWORDS_MTIME
    try:
        mtime = _AUTO_KEYWORDS_PATH.stat().st_mtime
    except FileNotFoundError:
        return False

    if mtime <= _AUTO_KEYWORDS_MTIME:
        return False  # file unchanged

    _KEYWORD_PATTERNS = _build_keyword_patterns()
    _AUTO_KEYWORDS_MTIME = mtime
    logger.info(
        f"auto_keywords.json updated (mtime={mtime:.0f}) — "
        f"keyword patterns reloaded across {len(_KEYWORD_PATTERNS)} topics"
    )
    return True


def _classify_by_keywords(title: str, description: str | None = None) -> list[str]:
    """
    Signal 3: keyword matching on title and optional description.

    Description is capped at 300 chars to limit cost without missing signal.
    Returns all matching topic labels (multi-label).
    """
    text = (title or '').strip()
    if description:
        text = text + ' ' + description[:300]
    if not text:
        return []

    matches = []
    for topic, patterns in _KEYWORD_PATTERNS.items():
        for pattern in patterns:
            if pattern.search(text):
                matches.append(topic)
                break  # one match per topic is enough
    return matches


# ---------------------------------------------------------------------------
# Signal 4: Locale + platform fallback (LOW confidence, conservative)
# Only fires when no other signal matched. Prevents known platforms from
# leaving items permanently unclassified.
# ---------------------------------------------------------------------------

def _classify_by_locale_fallback(lang_group: str | None, platform: str) -> list[str]:
    """
    Signal 4: conservative fallback based on locale + platform combo.

    Only assigns a label when confidence is high from platform identity alone.
    Bilibili zh-Hans → entertainment is safe: nearly all Bilibili content is
    consumer entertainment, and misclassification risk is low.
    """
    if lang_group == 'zh' and platform == 'bilibili':
        return ['entertainment']
    return []


# ---------------------------------------------------------------------------
# Signal 5: Surface key fallback (LOW–MEDIUM confidence)
# Fires only when all other signals produce nothing. Maps well-known surfaces
# (specific subreddits, news RSS feeds, Google News editions) to their dominant
# topic domain. Provides a baseline rather than leaving items permanently empty.
# ---------------------------------------------------------------------------

SURFACE_TOPIC_MAP: dict[str, list[str]] = {
    # Reddit — topic-specific subreddits (high confidence)
    'reddit_worldnews':      ['politics'],
    'reddit_news':           ['politics', 'society'],
    'reddit_economics':      ['finance', 'business'],
    'reddit_geopolitics':    ['politics'],
    'reddit_ukraine':        ['politics'],
    # Reddit — regional/country subreddits (medium confidence — general community boards)
    'reddit_europe':         ['politics', 'society'],
    'reddit_unitedkingdom':  ['politics', 'society'],
    'reddit_france':         ['politics', 'society'],
    'reddit_de':             ['politics', 'society'],
    'reddit_arabs':          ['politics', 'society'],
    'reddit_turkey':         ['politics', 'society'],
    'reddit_africa':         ['politics', 'society'],
    'reddit_india':          ['politics', 'society'],
    'reddit_philippines':    ['politics', 'society'],
    'reddit_askARussian':    ['politics', 'society'],
    'reddit_poland':         ['politics', 'society'],
    'reddit_brasil':         ['society'],
    'reddit_argentina':      ['society'],
    'reddit_mexico':         ['society'],
    'reddit_italy':          ['society'],
    'reddit_sweden':         ['society'],
    'reddit_es':             ['society'],
    'reddit_australia':      ['society'],
    'reddit_canada':         ['society'],
    'reddit_malaysia':       ['society'],
    # News RSS feeds — rss platform
    'g1_rss':                ['politics', 'society'],
    'aajtak_rss':            ['politics', 'society'],
    'cumhuriyet_rss':        ['politics', 'society'],
    'folha_rss':             ['politics', 'society'],
    'aljazeera_ar_rss':      ['politics'],
    'onet_rss':              ['politics', 'society'],
    'tass_rss':              ['politics'],
    'spiegel_rss':           ['politics', 'society'],
    'nhk_news_rss':          ['politics', 'society'],
    'tvn24_rss':             ['politics', 'society'],
    'ansa_rss':              ['politics', 'society'],
    'meduza_rss':            ['politics', 'society'],
    'france24_ar_rss':       ['politics'],
    'zeit_rss':              ['politics', 'society'],
    'bbc_hindi_rss':         ['politics', 'society'],
    'bbcarabic_rss':         ['politics', 'society'],
    'elpais_rss':            ['politics', 'society'],
    'lemonde_rss':           ['politics', 'society'],
    'dw_ru_rss':             ['politics', 'society'],
    'dw_ar_rss':             ['politics', 'society'],
    'rfi_rss':               ['politics', 'society'],
    '36kr_rss':              ['tech', 'business'],
    # News RSS feeds — generic_rss platform
    'portugal_news_rss':     ['politics', 'society'],
    'vnexpress_rss':         ['politics', 'society'],
    'sweden_news_rss':       ['politics', 'society'],
    'india_english_news_rss':['politics', 'society'],
    'globo_g1_rss':          ['politics', 'society'],
    'argentina_news_rss':    ['politics', 'society'],
    'nunl_rss':              ['politics', 'society'],
    'thailand_news_rss':     ['politics', 'society'],
    'philippines_news_rss':  ['politics', 'society'],
    # Dedicated news platform surfaces
    'bbc_news':              ['politics', 'society'],
    'guardian_news':         ['politics', 'society'],
    'reuters_news':          ['politics', 'business'],
    'aljazeera_news':        ['politics', 'society'],
    # Google News regional editions
    'google_news':           ['politics', 'society'],
    'google_news_de':        ['politics', 'society'],
    'google_news_br':        ['politics', 'society'],
    'google_news_fr':        ['politics', 'society'],
    'google_news_es':        ['politics', 'society'],
    'google_news_it':        ['politics', 'society'],
    'google_news_pt':        ['politics', 'society'],
    'google_news_kr':        ['politics', 'society'],
    'google_news_gb':        ['politics', 'society'],
    'google_news_jp':        ['politics', 'society'],
    'google_news_ua':        ['politics', 'society'],
    'google_news_in_en':     ['politics', 'society'],
    'google_news_ca':        ['politics', 'society'],
    'google_news_pk':        ['politics', 'society'],
    'google_news_mx':        ['politics', 'society'],
    'google_news_au':        ['politics', 'society'],
    'google_news_ng':        ['politics', 'society'],
    'allafrica_news':        ['politics', 'society'],
    'thehindu_news':         ['politics', 'society'],
    'almonitor_news':        ['politics'],
    'bloomberg_news':        ['finance', 'business'],
    # Social / trending platforms
    'weibo_hot':             ['society'],
    'baidu_hot':             ['society'],
    'naver_news_ranking':    ['politics', 'society'],
    'nicovideo_ranking':     ['entertainment'],
    'hatena_hotentry':       ['tech', 'society'],
    'wenxuecity_news':       ['society'],
    'wikipedia_most_read':   ['society'],
    # New surfaces (Phase 1)
    'sina_news':             ['politics', 'society'],
    'chinatimes_news':       ['politics', 'society'],
    # New surfaces (Phase 2 — NYT, pending API key)
    'nyt_news':              ['politics', 'society'],
    # New surfaces (Phase 3 — Playwright)
    'hk01_news':             ['politics', 'society'],
    'mirrormedia_news':      ['politics', 'society'],
}


def _classify_by_surface(surface_key: str) -> list[str]:
    """
    Signal 5: surface key fallback — fires only when all other signals fail.

    Maps well-known surfaces to their dominant topic domain. Returns [] for
    unknown surfaces (e.g. reddit_hot, google_trends) that are too generic
    to assign a meaningful label.
    """
    return SURFACE_TOPIC_MAP.get(surface_key, [])


# ---------------------------------------------------------------------------
# Main classification function
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
    Classify topic labels for a TrendItem. Multi-label, sorted.

    Returns a sorted list of labels from VALID_TOPICS, or [] if
    unclassifiable (item will be passed to the LLM pass).

    Signal priority:
      1. Bucket override  — highest confidence, set at crawl time
      2. Platform heuristic — high confidence, platform is topic-specific
      3. Keyword matching  — medium confidence, augments platform result
      4. Locale fallback   — conservative, prevents known gaps
    """
    # Signal 1: bucket override
    bucket_tags = set(_classify_by_bucket(bucket))
    if bucket_tags:
        # Bucket is authoritative — also run keywords for multi-label enrichment
        keyword_tags = set(_classify_by_keywords(title, description))
        combined = bucket_tags | keyword_tags
        return sorted(combined & VALID_TOPICS)

    # Signal 2: platform heuristic
    platform_tags = set(_classify_by_platform(platform))

    # Signal 3: keywords (always run — augments platform result)
    keyword_tags = set(_classify_by_keywords(title, description))

    combined = platform_tags | keyword_tags
    if combined:
        return sorted(combined & VALID_TOPICS)

    # Signal 4: conservative locale fallback
    fallback = _classify_by_locale_fallback(lang_group, platform)
    if fallback:
        return fallback

    # Signal 5: surface key fallback — last resort before giving up
    surface_tags = _classify_by_surface(surface_key)
    if surface_tags:
        return sorted(set(surface_tags) & VALID_TOPICS)

    return []  # unclassifiable — caller passes to LLM pass
