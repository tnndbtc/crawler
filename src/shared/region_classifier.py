"""
Content-based region classifier — heuristic pass.

Classifies TrendItems by what region the content is ABOUT,
using signals from language, platform, surface key, and title keywords.

Multi-label: an article can be about multiple regions.
Returns (content_regions: list[str], primary_region: str | None).
"""

import logging
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Signal 1: Language + Platform → region (HIGH confidence)
# ---------------------------------------------------------------------------

LANG_PLATFORM_MAP = {
    ('ja', 'yahoo_japan'): 'jp',
    ('ja', 'nhk_news_rss'): 'jp',
    ('ja', 'nicovideo'): 'jp',
    ('ja', 'hatena'): 'jp',
    ('ja', 'livedoor_news'): 'jp',
    ('ja', 'goo_news'): 'jp',
    ('ko', 'naver_news'): 'kr',
    ('ko', 'daum_news'): 'kr',
    ('zh', 'bilibili'): 'cn',
    ('zh', 'baidu'): 'cn',
    ('zh', 'weibo'): 'cn',
    ('zh', 'zhihu'): 'cn',
    ('zh', 'v2ex'): 'cn',
    ('zh', 'chinatimes'): 'tw',
    ('zh', 'mirrormedia'): 'tw',
    ('zh', 'ettoday'): 'tw',
    ('zh', 'hk01'): 'hk',
}

# Also match by platform alone for RSS feeds with lang_group
LANG_PLATFORM_BY_PLATFORM = {
    '36kr_rss': ('zh', 'cn'),
}


def _classify_by_lang_platform(lang_group: str | None, platform: str) -> str | None:
    """Signal 1: language + platform combo."""
    if not lang_group:
        return None
    return LANG_PLATFORM_MAP.get((lang_group, platform))


# ---------------------------------------------------------------------------
# Signal 2: Regional Reddit subs (HIGH confidence)
# ---------------------------------------------------------------------------

REDDIT_SURFACE_MAP = {
    'reddit_india': 'in',
    'reddit_france': 'fr',
    'reddit_de': 'de',
    'reddit_askARussian': 'ru',
    'reddit_poland': 'pl',
    'reddit_italy': 'it',
    'reddit_brasil': 'br',
    'reddit_arabs': 'ar',
    'reddit_turkey': 'tr',
    'reddit_es': 'es',
    'reddit_mexico': 'es',  # Mexico → es (Spain/LatAm)
    'reddit_argentina': 'ar_latam',
}


def _classify_by_reddit_sub(platform: str, surface_key: str) -> str | None:
    """Signal 2: regional reddit subreddit."""
    if platform != 'reddit':
        return None
    return REDDIT_SURFACE_MAP.get(surface_key)


# ---------------------------------------------------------------------------
# Signal 3: Regional RSS feeds (MEDIUM-HIGH confidence)
# ---------------------------------------------------------------------------

RSS_SURFACE_MAP = {
    # Brazil
    'g1_rss': 'br', 'folha_rss': 'br', 'bbcbrasil_rss': 'br',
    'uol_rss': 'br', 'globo_g1_rss': 'br',
    # Germany
    'spiegel_rss': 'de', 'zeit_rss': 'de', 'tagesschau_rss': 'de',
    'sueddeutsche_rss': 'de', 'dw_de_rss': 'de',
    # France
    'lemonde_rss': 'fr', 'france24_rss': 'fr', 'rfi_rss': 'fr',
    'lefigaro_rss': 'fr', 'leparisien_rss': 'fr',
    # Spain
    'elpais_rss': 'es', 'elmundo_rss': 'es', 'bbcmundo_rss': 'es',
    'infobae_rss': 'es',
    # Russia
    'tass_rss': 'ru', 'meduza_rss': 'ru', 'moscowtimes_rss': 'ru',
    'bbc_russian_rss': 'ru', 'dw_ru_rss': 'ru',
    # India
    'aajtak_rss': 'in', 'bbc_hindi_rss': 'in', 'zeenews_hindi_rss': 'in',
    'navbharattimes_rss': 'in', 'ndtv_hindi_rss': 'in',
    'india_english_news_rss': 'in',
    # Italy
    'ansa_rss': 'it', 'repubblica_rss': 'it', 'corriere_rss': 'it',
    'sole24ore_rss': 'it',
    # Turkey
    'bbcturkce_rss': 'tr', 'hurriyet_rss': 'tr', 'dw_tr_rss': 'tr',
    'dailysabah_rss': 'tr', 'cumhuriyet_rss': 'tr',
    # Arab world
    'aljazeera_ar_rss': 'ar', 'bbcarabic_rss': 'ar', 'alarabiya_rss': 'ar',
    'france24_ar_rss': 'ar', 'dw_ar_rss': 'ar',
    # Poland
    'onet_rss': 'pl', 'tvn24_rss': 'pl', 'pap_rss': 'pl', 'wyborcza_rss': 'pl',
    # Indonesia
    'kompas_rss': 'id', 'antara_rss': 'id', 'bbcindonesia_rss': 'id',
    'tempo_rss': 'id',
    # Japan
    'nhk_news_rss': 'jp',
    # China
    '36kr_rss': 'cn', 'netease_news_rss': 'cn',
    # Taiwan
    'chinatimes_news': 'tw', 'mirrormedia_news': 'tw', 'ettoday_rss': 'tw',
    # Hong Kong
    'hk01_news': 'hk',
    # Others
    'nunl_rss': 'nl',
    'sweden_news_rss': 'se',
    'philippines_news_rss': 'ph',
    'vnexpress_rss': 'vn',
    'thailand_news_rss': 'th',
    'malaysia_news_rss': 'my',
    'portugal_news_rss': 'pt',
    'argentina_news_rss': 'ar_latam',
    # Korea
    'naver_news_ranking': 'kr', 'daum_news_trending': 'kr',
    # Japan extras
    'yahoo_jp_ranking': 'jp', 'goo_news_ranking': 'jp',
    'livedoor_news_ranking': 'jp', 'hatena_hotentry': 'jp',
    # China extras
    'wenxuecity_news': None,  # wenxuecity covers global — use keywords
    'bilibili_trending': 'cn', 'baidu_hot': 'cn',
    'weibo_sogou': 'cn', 'weibo_hot': 'cn', 'zhihu_hot': 'cn',
}


def _classify_by_rss_source(surface_key: str) -> str | None:
    """Signal 3: regional RSS/platform surface."""
    return RSS_SURFACE_MAP.get(surface_key)


# ---------------------------------------------------------------------------
# Signal 4: Keyword matching on title (MEDIUM confidence)
# ---------------------------------------------------------------------------

KEYWORD_MAP = {
    'cn': ['中国', '北京', '上海', '深圳', '广州', '习近平', 'China', 'Beijing',
           'Shanghai', 'Shenzhen', 'Guangzhou', 'Chinese'],
    'jp': ['日本', '東京', '大阪', '横浜', 'Japan', 'Tokyo', 'Osaka', 'Yokohama',
           'Shinzo Abe'],
    'kr': ['한국', '서울', '부산', 'Korea', 'South Korea', 'Seoul', 'Busan'],
    'us': ['美国', 'Trump', 'Biden', 'White House', 'Washington DC',
           'United States', 'U.S.', 'New York', 'California'],
    'ru': ['Россия', 'Москва', 'Путин', 'Кремль', 'Russia', 'Moscow', 'Putin',
           'Kremlin', 'Saint Petersburg'],
    'in': ['India', 'Modi', 'Delhi', 'New Delhi', 'Mumbai', 'Bangalore', 'भारत'],
    'de': ['Deutschland', 'Berlin', 'München', 'Hamburg', 'Frankfurt',
           'Scholz', 'Merz', 'Germany', 'German', 'Bundesliga'],
    'fr': ['France', 'Paris', 'Macron', 'Lyon', 'Marseille', 'français',
           'French', 'Elysée'],
    'br': ['Brasil', 'Brasília', 'Lula', 'São Paulo', 'Rio de Janeiro',
           'Brazil', 'Brazilian', 'Bolsonaro'],
    'es': ['España', 'Madrid', 'Barcelona', 'Valencia', 'Sevilla', 'Sánchez',
           'Spain', 'Spanish'],
    'it': ['Italia', 'Roma', 'Milano', 'Napoli', 'Venezia', 'Meloni', 'Italy',
           'Italian'],
    'tr': ['Türkiye', 'Ankara', 'İstanbul', 'Istanbul', 'Erdoğan', 'Turkey',
           'Turkish'],
    'ar': ['السعودية', 'مصر', 'دبي', 'أبوظبي', 'الرياض', 'القاهرة',
           'Saudi', 'Egypt', 'Dubai', 'Abu Dhabi', 'UAE', 'Riyadh', 'Cairo',
           'Middle East'],
    'id': ['Indonesia', 'Jakarta', 'Jokowi', 'Prabowo', 'Indonesian', 'Bali'],
    'pl': ['Polska', 'Warszawa', 'Warsaw', 'Kraków', 'Tusk', 'Poland', 'Polish'],
    'nl': ['Nederland', 'Amsterdam', 'Rotterdam', 'Den Haag', 'Netherlands',
           'Dutch'],
    'se': ['Sverige', 'Stockholm', 'Göteborg', 'Sweden', 'Swedish', 'Gothenburg'],
    'ph': ['Pilipinas', 'Manila', 'Quezon', 'Marcos', 'Philippines', 'Filipino'],
    'vn': ['Việt Nam', 'Hà Nội', 'Sài Gòn', 'Vietnam', 'Vietnamese',
           'Ho Chi Minh'],
    'th': ['ประเทศไทย', 'กรุงเทพ', 'เชียงใหม่', 'Bangkok', 'Chiang Mai',
           'Thailand', 'Thai'],
    'my': ['Malaysia', 'Kuala Lumpur', 'Anwar', 'Malaysian'],
    'pt': ['Portugal', 'Lisboa', 'Lisbon', 'Porto', 'Portuguese'],
    'ar_latam': ['Argentina', 'Buenos Aires', 'Milei', 'Argentino', 'Argentine'],
    'tw': ['台灣', '台北', '台中', '高雄', '新北', '桃園', '台南', 'Taiwan', 'Taipei',
           'Kaohsiung', 'Taichung', 'Tainan'],
    'hk': ['香港', '九龍', '新界', 'Hong Kong', 'Hongkong', 'HK', 'Kowloon'],
}

# Pre-compile case-insensitive patterns
_KEYWORD_PATTERNS: dict[str, list[re.Pattern]] = {}
for _region, _keywords in KEYWORD_MAP.items():
    _KEYWORD_PATTERNS[_region] = [
        re.compile(re.escape(kw), re.IGNORECASE) for kw in _keywords
    ]


def _classify_by_keywords(title: str) -> set[str]:
    """Signal 4: keyword matching. Returns set of ALL matching regions."""
    if not title:
        return set()
    matches = set()
    for region, patterns in _KEYWORD_PATTERNS.items():
        for pattern in patterns:
            if pattern.search(title):
                matches.add(region)
                break  # one match per region is enough
    return matches


# ---------------------------------------------------------------------------
# Main classification function
# ---------------------------------------------------------------------------

def classify_content_regions(
    lang_group: str | None,
    platform: str,
    surface_key: str,
    title: str,
) -> tuple[list[str], str | None]:
    """
    Classify which regions the content is about. Multi-label.

    Returns (content_regions, primary_region).
    content_regions: sorted list of region keys, e.g. ['cn', 'us']
    primary_region: first non-US region, or first region if all US.
    Returns ([], None) if unclassifiable.
    """
    regions = set()

    # Signal 1: lang + platform (HIGH confidence)
    result = _classify_by_lang_platform(lang_group, platform)
    if result:
        regions.add(result)

    # Signal 2: regional reddit (HIGH confidence)
    result = _classify_by_reddit_sub(platform, surface_key)
    if result:
        regions.add(result)

    # Signal 3: regional RSS (MEDIUM-HIGH confidence)
    result = _classify_by_rss_source(surface_key)
    if result:
        regions.add(result)

    # Signal 4: keywords in title (MEDIUM confidence)
    keyword_matches = _classify_by_keywords(title)
    regions.update(keyword_matches)

    if not regions:
        return [], None

    regions_list = sorted(regions)
    # primary_region: prefer non-US
    non_us = [r for r in regions_list if r != 'us']
    primary = non_us[0] if non_us else regions_list[0]

    return regions_list, primary
