"""
Al Jazeera News surface collector.

Fetches latest news from Al Jazeera RSS feed.

Source: https://www.aljazeera.com/xml/rss/all.xml
Schedule: Every 20 minutes
Max items: 40

Surface type: news
Bucket: region_local (major international news source with Middle East focus)

Migrated from: /home/tnnd/data/code/trend/trend_agent/collectors/al_jazeera.py
"""

import logging
from typing import Optional, Tuple, List

from .base_rss import collect_rss_feed
from .collector_interface import CollectedItem

logger = logging.getLogger(__name__)

# Al Jazeera RSS feed URL
ALJAZEERA_RSS_URL = "https://www.aljazeera.com/xml/rss/all.xml"


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect latest news from Al Jazeera RSS feed.

    Config params (from TrendSurface.config_json):
        - locale: Locale code (default: 'en-US')
        - rss_url: Override default RSS URL (optional)

    Args:
        config: Surface configuration
        cursor: Last seen entry ID
        limit: Maximum items to collect (default: 40)

    Returns:
        (items, next_cursor)
    """
    rss_url = config.get('rss_url', ALJAZEERA_RSS_URL)
    locale = config.get('locale', 'en-US')

    logger.info(f"Collecting Al Jazeera News: limit={limit}")

    return await collect_rss_feed(
        rss_url=rss_url,
        source='aljazeera',
        config=config,
        cursor=cursor,
        limit=limit,
        locale=locale
    )
