"""
Hatena Bookmark Hot Entry surface collector.

Fetches hot entries (popular bookmarks) from Hatena Bookmark (b.hatena.ne.jp),
a Japanese social bookmarking service. No authentication required.

Supports optional category filtering via config.

Surface type: ranking
Bucket: hot_now
Locale: ja-JP
"""

import logging
from typing import Optional, Tuple, List

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

# Hatena Bookmark hot entry base URL
HATENA_HOTENTRY_BASE = "https://b.hatena.ne.jp/hotentry"


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect hot entries from Hatena Bookmark.

    Config params (from TrendSurface.config_json):
        - category: Optional category slug (e.g. "it", "general", "social", "economics",
          "entertainment", "game", "fun", "science"). If omitted, fetches all-category hot entries.

    Args:
        config: Surface configuration
        cursor: Pagination cursor (unused — API returns full list)
        limit: Maximum items to collect

    Returns:
        (items, None) — no pagination cursor

    Raises:
        httpx.HTTPError: On API request failures
    """
    category = config.get("category")

    # Build endpoint URL — optionally scoped to a category
    if category:
        url = f"{HATENA_HOTENTRY_BASE}/{category}.json"
    else:
        url = f"{HATENA_HOTENTRY_BASE}.json"

    logger.info(f"Fetching Hatena hot entries (category={category or 'all'})...")

    async with RateLimitedClient(timeout=30.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()

    # API may return either:
    #   - a list of entries directly
    #   - an object with an "items" key
    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict):
        entries = data.get("items", [])
    else:
        logger.warning("Hatena API returned unexpected format")
        return [], None

    items: List[CollectedItem] = []
    for rank, entry in enumerate(entries[:limit], start=1):
        title = entry.get("title", "Untitled")

        # "url" is the bookmarked (original) page URL
        content_url = entry.get("url", "")

        # "entry_url" is the Hatena bookmark page for this entry
        entry_url = entry.get("entry_url", "")

        description = entry.get("description", "")

        # Bookmark count may be keyed as "bookmarks_count" or "count"
        bookmarks = entry.get("bookmarks_count") or entry.get("count") or 0

        # Timestamp may be keyed as "timestamp" or "date"
        timestamp = entry.get("timestamp") or entry.get("date")

        item: CollectedItem = {
            "title": title,
            "description": description[:1000] if description else None,
            "url": content_url,
            "locale": "ja-JP",
            "rank_position": rank,
            "engagement_signals": {
                "bookmarks": bookmarks,
            },
            "raw_payload": {
                **entry,
                "_metadata": {
                    "entry_url": entry_url,
                    "category": category,
                },
            },
        }

        # Add timestamp only when present (format varies: ISO8601 or human-readable)
        if timestamp:
            item["published_at"] = str(timestamp)

        items.append(item)

    logger.info(f"Collected {len(items)} items from Hatena")
    return items, None
