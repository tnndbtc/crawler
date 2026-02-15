"""
IGN News surface collector.

Fetches latest gaming news from IGN RSS feed.

Source: https://www.ign.com/articles?tags=news
Schedule: Every 30 minutes
Max items: 30

Surface type: news
Bucket: category_gaming (video game news and reviews)

Migrated from: /home/tnnd/data/code/trend/trend_agent/collectors/ign.py
"""

import logging
from typing import Optional, Tuple, List

from .base_rss import collect_rss_feed
from .collector_interface import CollectedItem

logger = logging.getLogger(__name__)

# IGN RSS feed URL (articles with news tag)
IGN_RSS_URL = "https://feeds.feedburner.com/ign/news"


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect latest gaming news from IGN RSS feed.

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
    rss_url = config.get('rss_url', IGN_RSS_URL)
    locale = config.get('locale', 'en-US')

    logger.info(f"Collecting IGN News: limit={limit}")

    return await collect_rss_feed(
        rss_url=rss_url,
        source='ign',
        config=config,
        cursor=cursor,
        limit=limit,
        locale=locale
    )
