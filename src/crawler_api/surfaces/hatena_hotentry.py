"""
Hatena Bookmark Hot Entry surface collector.

Fetches hot entries (popular bookmarks) from Hatena Bookmark (b.hatena.ne.jp),
a Japanese social bookmarking service. No authentication required.

Uses the RSS feed endpoint (JSON API was removed).
URL: https://b.hatena.ne.jp/hotentry?mode=rss

Surface type: ranking
Bucket: hot_now
Locale: ja-JP
"""

import logging
from typing import Optional, Tuple, List

from .collector_interface import CollectedItem
from .base_rss import collect_rss_feed

logger = logging.getLogger(__name__)

HATENA_RSS_URL = "https://b.hatena.ne.jp/hotentry?mode=rss"
HATENA_CATEGORY_RSS_URL = "https://b.hatena.ne.jp/hotentry/{category}?mode=rss"


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect hot entries from Hatena Bookmark via RSS feed.

    The JSON API (hotentry.json) was removed by Hatena. This collector
    uses the RSS feed which remains publicly available.

    Config params (from TrendSurface.config_json):
        - category: Optional category slug (e.g. "it", "general", "social",
          "economics", "entertainment", "game", "fun", "science").
          If omitted, fetches all-category hot entries.

    Args:
        config: Surface configuration
        cursor: Pagination cursor (unused — RSS returns full list)
        limit: Maximum items to collect

    Returns:
        (items, None) — no pagination cursor

    Raises:
        httpx.HTTPError: On feed request failures
    """
    category = config.get("category")

    if category:
        feed_url = HATENA_CATEGORY_RSS_URL.format(category=category)
    else:
        feed_url = HATENA_RSS_URL

    logger.info(f"Fetching Hatena hot entries via RSS (category={category or 'all'})...")

    items, next_cursor = await collect_rss_feed(
        rss_url=feed_url,
        source="hatena",
        config=config,
        cursor=cursor,
        limit=limit,
        locale="ja-JP",
    )

    logger.info(f"Collected {len(items)} items from Hatena")
    return items, None
