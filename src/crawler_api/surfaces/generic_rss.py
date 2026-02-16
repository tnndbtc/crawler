"""
Generic RSS collector for arbitrary RSS feeds.

Feature B (from /tmp/t3):
- Reusable RSS collector that accepts feed URLs from config
- Aggregates items from multiple feeds
- Deduplicates by URL
- Normalizes content into standard TrendItem format

Configuration (config_json):
{
    "feed_urls": [
        "https://feeds.example.com/feed1.xml",
        "https://feeds.example.com/feed2.xml"
    ],
    "locale": "en-US",  // Optional, default: en-US
    "source_name": "generic_rss"  // Optional, default: generic_rss
}

Usage:
- Add TrendSurface with entrypoint: "crawler_api.surfaces.generic_rss:collect"
- Configure feed_urls in config_json
- Worker will automatically collect from all feeds
"""

import logging
from typing import List, Optional, Tuple, Dict, Any

from .base_rss import collect_rss_feed
from .collector_interface import CollectedItem

logger = logging.getLogger(__name__)


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect items from multiple RSS feeds.

    Feature B (from /tmp/t3):
    - Generic RSS collector for arbitrary feed URLs
    - Takes feed_urls from config
    - Aggregates and deduplicates items from all feeds

    Args:
        config: Surface configuration with feed_urls list
        cursor: Last seen entry ID (for deduplication, not used for multi-feed)
        limit: Maximum items to collect (distributed across feeds)

    Returns:
        Tuple of (items, next_cursor)

    Example config:
        {
            "feed_urls": [
                "https://techcrunch.com/feed/",
                "https://feeds.arstechnica.com/arstechnica/index"
            ],
            "locale": "en-US",
            "source_name": "tech_news"
        }
    """
    # Extract config
    feed_urls = config.get('feed_urls', [])
    locale = config.get('locale', 'en-US')
    source_name = config.get('source_name', 'generic_rss')

    if not feed_urls:
        logger.error("No feed_urls configured in config_json")
        return [], None

    logger.info(
        f"Generic RSS collector: collecting from {len(feed_urls)} feed(s) "
        f"(source={source_name}, limit={limit})"
    )

    # Collect from all feeds
    all_items: List[CollectedItem] = []
    seen_urls: Dict[str, bool] = {}  # Dedupe by URL
    items_per_feed = max(limit // len(feed_urls), 10)  # Distribute limit across feeds

    for idx, feed_url in enumerate(feed_urls):
        try:
            # Collect from this feed
            items, _ = await collect_rss_feed(
                rss_url=feed_url,
                source=source_name,
                config=config,
                cursor=None,  # Don't use cursor for multi-feed (complex state management)
                limit=items_per_feed,
                locale=locale
            )

            # Deduplicate by URL
            for item in items:
                url = item['url']
                if url not in seen_urls:
                    seen_urls[url] = True
                    all_items.append(item)
                    logger.debug(f"Added item: {item['title'][:50]}...")

                    # Stop if we've reached the limit
                    if len(all_items) >= limit:
                        break

            if len(all_items) >= limit:
                break

        except Exception as e:
            logger.error(
                f"Error collecting from feed {feed_url}: {e}",
                exc_info=True
            )
            # Continue to next feed even if this one fails

    # Re-rank items after aggregation (sorted by original rank across feeds)
    # Assign new rank_position based on order in all_items list
    for idx, item in enumerate(all_items, start=1):
        item['rank_position'] = idx

    logger.info(
        f"Generic RSS collector: collected {len(all_items)} items "
        f"from {len(feed_urls)} feed(s) (after deduplication)"
    )

    # Cursor: Use first item's ID (for consistency with single-feed behavior)
    # Note: Multi-feed cursors are complex, so we don't use them for deduplication
    # Deduplication happens at the database level via canonical_hash
    next_cursor = all_items[0]['external_id'] if all_items else None

    return all_items, next_cursor
