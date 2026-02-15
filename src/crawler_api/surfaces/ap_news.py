"""
Associated Press (AP) News surface collector.

Fetches top news from AP RSS feed.

Source: https://rss.apnews.com/rss/topnews
Schedule: Every 20 minutes
Max items: 40

Surface type: news
Bucket: region_local (major US/international news source)

Migrated from: /home/tnnd/data/code/trend/trend_agent/collectors/ap_news.py
"""

import logging
from typing import Optional, Tuple, List

from .base_rss import collect_rss_feed
from .collector_interface import CollectedItem

logger = logging.getLogger(__name__)

# AP News RSS feed URL
AP_RSS_URL = "https://rss.apnews.com/rss/topnews"


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect latest news from Associated Press RSS feed.

    Config params (from TrendSurface.config_json):
        - locale: Locale code (default: 'en-US')
        - rss_url: Override default RSS URL (optional)
        - category: AP category (optional: us, world, politics, sports, entertainment, business, technology, health, science, oddities)

    Args:
        config: Surface configuration
        cursor: Last seen entry ID
        limit: Maximum items to collect (default: 40)

    Returns:
        (items, next_cursor)
    """
    rss_url = config.get('rss_url', AP_RSS_URL)
    locale = config.get('locale', 'en-US')

    # Support category filtering
    category = config.get('category')
    if category:
        rss_url = f"https://rss.apnews.com/rss/{category}"

    logger.info(f"Collecting AP News: limit={limit}")

    return await collect_rss_feed(
        rss_url=rss_url,
        source='ap_news',
        config=config,
        cursor=cursor,
        limit=limit,
        locale=locale
    )
