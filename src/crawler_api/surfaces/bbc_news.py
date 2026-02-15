"""
BBC News surface collector.

Fetches latest news from BBC's main RSS feed.

Source: http://feeds.bbci.co.uk/news/rss.xml
Schedule: Every 20 minutes
Max items: 40

Surface type: news
Bucket: region_local (major international news source)

Migrated from: /home/tnnd/data/code/trend/trend_agent/collectors/bbc.py
"""

import logging
from typing import Optional, Tuple, List

from .base_rss import collect_rss_feed
from .collector_interface import CollectedItem

logger = logging.getLogger(__name__)

# BBC RSS feed URL
BBC_RSS_URL = "http://feeds.bbci.co.uk/news/rss.xml"


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect latest news from BBC News RSS feed.

    Config params (from TrendSurface.config_json):
        - locale: Locale code (default: 'en-GB' for BBC)
        - rss_url: Override default RSS URL (optional)

    Args:
        config: Surface configuration
        cursor: Last seen entry ID (for deduplication)
        limit: Maximum items to collect (default: 40)

    Returns:
        (items, next_cursor)

    Raises:
        Exception: On RSS feed parsing errors

    Example:
        >>> items, cursor = await collect(config={}, cursor=None, limit=40)
        >>> len(items) <= 40
        True
        >>> items[0]['locale']
        'en-GB'
    """
    # Allow config to override RSS URL
    rss_url = config.get('rss_url', BBC_RSS_URL)

    # BBC is UK-based, use en-GB locale
    locale = config.get('locale', 'en-GB')

    logger.info(f"Collecting BBC News: limit={limit}")

    # Use base RSS collector
    return await collect_rss_feed(
        rss_url=rss_url,
        source='bbc',
        config=config,
        cursor=cursor,
        limit=limit,
        locale=locale
    )
