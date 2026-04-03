"""
V2EX Hot Topics surface collector.

Fetches the hot topics from V2EX, a Chinese tech/developer community,
using the public hot topics API. No authentication required.

Surface type: ranking
Bucket: hot_now
Locale: zh-Hans
"""

import logging
from typing import Optional, Tuple, List
from datetime import datetime, timezone

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

# V2EX hot topics API endpoint
V2EX_HOT_URL = "https://www.v2ex.com/api/topics/hot.json"


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect hot topics from V2EX using the public API.

    Config params (from TrendSurface.config_json):
        None required. API returns the current hot list without pagination.

    Args:
        config: Surface configuration (no required params)
        cursor: Pagination cursor (unused — V2EX returns full hot list)
        limit: Maximum items to collect

    Returns:
        (items, None) — no pagination cursor

    Raises:
        httpx.HTTPError: On API request failures
    """
    logger.info("Fetching V2EX hot topics...")

    async with RateLimitedClient(timeout=30.0) as client:
        response = await client.get(V2EX_HOT_URL)
        response.raise_for_status()
        data = response.json()

    # API returns a JSON array of topic objects
    if not isinstance(data, list):
        logger.warning("V2EX API returned unexpected format (expected list)")
        return [], None

    items: List[CollectedItem] = []
    for rank, topic in enumerate(data[:limit], start=1):
        topic_id = topic.get("id")
        title = topic.get("title", "Untitled")
        url = topic.get("url", "")
        content = topic.get("content", "")
        replies = topic.get("replies", 0)

        # Extract member info
        member = topic.get("member", {})
        author = member.get("username", "") if isinstance(member, dict) else ""

        # Extract node info
        node = topic.get("node", {})
        node_title = node.get("title", "") if isinstance(node, dict) else ""

        # Parse timestamps
        created = topic.get("created")
        published_at = None
        if created:
            try:
                published_at = datetime.fromtimestamp(created, tz=timezone.utc).isoformat()
            except (ValueError, OSError):
                pass

        item: CollectedItem = {
            "external_id": str(topic_id) if topic_id is not None else None,
            "title": title,
            "description": content[:1000] if content else None,
            "url": url,
            "locale": "zh-Hans",
            "published_at": published_at,
            "rank_position": rank,
            "engagement_signals": {
                "comments": replies,
            },
            "raw_payload": {
                **topic,
                "_metadata": {
                    "author": author,
                    "node_title": node_title,
                },
            },
        }
        items.append(item)

    logger.info(f"Collected {len(items)} items from V2EX")
    return items, None
