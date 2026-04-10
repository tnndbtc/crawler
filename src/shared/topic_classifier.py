"""
Content-based topic classifier — heuristic pass.

Classifies TrendItems by topic using signals from bucket, platform,
surface key, lang_group, and title/description keywords.

Multi-label: an article can have multiple topic tags.

Fixed vocabulary (12 labels):
  politics, finance, ai, tech, science, entertainment,
  sports, business, crime, society, health, environment

Returns [] when unclassifiable — those items are passed to the LLM pass.
"""

import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Path to the auto-generated keyword file produced by keyword_harvest_worker.py
_AUTO_KEYWORDS_PATH = Path(__file__).parent.parent.parent / 'config' / 'auto_keywords.json'

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
    ],
    'science': [
        'researchers', 'scientists', 'research paper', 'new study',
        'discovered', 'published in', 'Nature ', 'Science journal',
        'genome', 'quantum', 'CERN', 'telescope', 'NASA ', 'SpaceX',
        'climate change', 'carbon emissions', 'new species', 'biology',
        'physics', 'chemistry', 'experiment',
    ],
    'health': [
        'vaccine', 'vaccination', 'virus', 'pandemic', 'outbreak',
        ' FDA', ' WHO', 'cancer', ' drug ', 'hospital', 'patient',
        'treatment', 'mental health', 'obesity', 'diabetes', 'COVID',
        'disease', 'epidemic', 'public health',
    ],
    'sports': [
        'NFL', 'NBA', 'FIFA', 'Premier League', 'Champions League',
        'Olympics', 'World Cup', 'tournament', 'championship',
        'transfer', 'match result', 'season opener', 'Grand Slam',
        'Formula 1', 'F1 ', 'Super Bowl', 'playoff',
    ],
    'entertainment': [
        ' movie', ' film', 'Netflix', 'Disney', 'box office', 'Oscar',
        'Grammy', 'music album', 'singer', ' actor', 'celebrity',
        'Taylor Swift', 'BTS', 'K-pop', 'anime', 'video game',
        'PlayStation', 'Xbox', 'Nintendo', 'streaming',
    ],
    'business': [
        'merger', 'acquisition', 'layoff', 'layoffs', 'CEO', 'revenue',
        'quarterly results', 'supply chain', 'logistics', 'retail sales',
        'brand', 'bankruptcy', 'valuation', 'venture capital', 'funding round',
        # China business
        'Alibaba', 'Tencent', 'JD.com', 'Pinduoduo', 'Meituan', 'Xiaomi',
        'Ant Group', 'DiDi', 'Chinese EV', 'lithium supply chain',
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
        'immigration', 'migrants', 'protest', 'protesters', 'strike',
        'labor union', 'inequality', 'poverty', 'education', 'university',
        'demographic', 'religion', 'culture war', 'social media',
    ],
}

# ---------------------------------------------------------------------------
# Merge static KEYWORD_MAP with auto_keywords.json (if present) at module load.
# auto_keywords.json is generated weekly by keyword_harvest_worker.py.
# On first deploy (file absent) or corrupt write, falls back to static only.
# ---------------------------------------------------------------------------

def _load_auto_keywords() -> dict[str, list[str]]:
    try:
        with open(_AUTO_KEYWORDS_PATH) as f:
            data = json.load(f)
        return data.get('keywords', {})
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


# Pre-compile case-insensitive patterns once at module load time
_KEYWORD_PATTERNS: dict[str, list[re.Pattern]] = _build_keyword_patterns()


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

    return []  # unclassifiable — caller passes to LLM pass
