"""
Variety News surface collector.

Fetches latest entertainment industry news from Variety RSS feed.

Source: https://variety.com/feed/
Schedule: Every 30 minutes
Max items: 30

Surface type: news
Bucket: category_entertainment (film, TV, entertainment industry)

Migrated from: /home/tnnd/data/code/trend/trend_agent/collectors/variety.py
"""

import logging
from typing import Optional, Tuple, List

from .base_rss import collect_rss_feed
from .collector_interface import CollectedItem

logger = logging.getLogger(__name__)

# Variety RSS feed URL
VARIETY_RSS_URL = "https://variety.com/feed/"


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect latest entertainment news from Variety RSS feed.

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
    rss_url = config.get('rss_url', VARIETY_RSS_URL)
    locale = config.get('locale', 'en-US')

    logger.info(f"Collecting Variety News: limit={limit}")

    return await collect_rss_feed(
        rss_url=rss_url,
        source='variety',
        config=config,
        cursor=cursor,
        limit=limit,
        locale=locale
    )
