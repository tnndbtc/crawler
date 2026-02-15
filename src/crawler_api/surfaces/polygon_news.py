"""
Polygon News surface collector.

Fetches latest gaming and entertainment news from Polygon RSS feed.

Source: https://www.polygon.com/rss/index.xml
Schedule: Every 30 minutes
Max items: 30

Surface type: news
Bucket: category_gaming (video games, entertainment, culture)

Migrated from: /home/tnnd/data/code/trend/trend_agent/collectors/polygon.py
"""

import logging
from typing import Optional, Tuple, List

from .base_rss import collect_rss_feed
from .collector_interface import CollectedItem

logger = logging.getLogger(__name__)

# Polygon RSS feed URL
POLYGON_RSS_URL = "https://www.polygon.com/rss/index.xml"


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect latest gaming news from Polygon RSS feed.

    Config params (from TrendSurface.config_json):
        - locale: Locale code (default: 'en-US')
        - rss_url: Override default RSS URL (optional)

    Args:
        config: Surface configuration
        cursor: Last seen entry ID
        limit: Maximum items to collect (default: 30)

    Returns:
        (items, next_cursor)
    """
    rss_url = config.get('rss_url', POLYGON_RSS_URL)
    locale = config.get('locale', 'en-US')

    logger.info(f"Collecting Polygon News: limit={limit}")

    return await collect_rss_feed(
        rss_url=rss_url,
        source='polygon',
        config=config,
        cursor=cursor,
        limit=limit,
        locale=locale
    )
