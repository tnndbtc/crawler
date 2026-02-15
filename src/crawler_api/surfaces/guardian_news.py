"""
The Guardian News surface collector.

Fetches world news from The Guardian RSS feed.

Source: https://www.theguardian.com/world/rss
Schedule: Every 20 minutes
Max items: 40

Surface type: news
Bucket: region_local (major UK/international news source)

Migrated from: /home/tnnd/data/code/trend/trend_agent/collectors/guardian.py
"""

import logging
from typing import Optional, Tuple, List

from .base_rss import collect_rss_feed
from .collector_interface import CollectedItem

logger = logging.getLogger(__name__)

# The Guardian RSS feed URL
GUARDIAN_RSS_URL = "https://www.theguardian.com/world/rss"


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect latest news from The Guardian RSS feed.

    Config params (from TrendSurface.config_json):
        - locale: Locale code (default: 'en-GB')
        - rss_url: Override default RSS URL (optional)
        - section: Guardian section (optional: world, uk, us, politics, business, technology, environment, science, media, education)

    Args:
        config: Surface configuration
        cursor: Last seen entry ID
        limit: Maximum items to collect (default: 40)

    Returns:
        (items, next_cursor)
    """
    rss_url = config.get('rss_url', GUARDIAN_RSS_URL)
    locale = config.get('locale', 'en-GB')  # Guardian is UK-based

    # Support section filtering
    section = config.get('section')
    if section:
        rss_url = f"https://www.theguardian.com/{section}/rss"

    logger.info(f"Collecting Guardian News: limit={limit}")

    return await collect_rss_feed(
        rss_url=rss_url,
        source='guardian',
        config=config,
        cursor=cursor,
        limit=limit,
        locale=locale
    )
