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

import logging
import re

logger = logging.getLogger(__name__)

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
        '政治', '选举', '制裁', '外交',
    ],
    'finance': [
        'Fed ', 'Federal Reserve', 'rate hike', 'rate cut', 'inflation',
        ' GDP', 'recession', 'earnings', ' IPO', ' stock ', 'stocks',
        ' bond', ' yield', 'crypto', 'bitcoin', ' ETF', 'hedge fund',
        'IMF', ' ECB', 'deficit', 'debt ceiling', 'stock market',
        'S&P', 'Nasdaq', 'Dow ', 'Wall Street', 'interest rate',
        '美联储', '通胀', '经济', '股市', '加息', '降息',
    ],
    'ai': [
        'LLM', ' GPT', 'Claude', 'Gemini', 'Llama', ' AI ', ' AI,', ' AI.',
        'artificial intelligence', 'machine learning', 'deep learning',
        'neural network', 'transformer model', 'benchmark', 'AGI',
        'Anthropic', 'OpenAI', 'DeepMind', 'Mistral', 'xAI', 'Grok',
        'AI Act', 'large language model', 'foundation model',
        'diffusion model', 'generative AI', 'AI model', 'AI system',
    ],
    'tech': [
        'Apple', 'Google', 'Microsoft', 'Meta', 'Amazon', 'Tesla',
        'startup', 'software', 'hardware', ' chip', 'semiconductor',
        'iPhone', 'Android', 'open source', 'developer', 'programming',
        'cloud computing', 'cybersecurity', 'data breach', 'quantum computing',
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

# Pre-compile case-insensitive patterns once at module load time
_KEYWORD_PATTERNS: dict[str, list[re.Pattern]] = {
    topic: [re.compile(re.escape(kw), re.IGNORECASE) for kw in keywords]
    for topic, keywords in KEYWORD_MAP.items()
}


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
