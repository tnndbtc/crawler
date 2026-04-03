"""
Bilibili Trending surface collector.

Fetches trending videos from Bilibili using the public ranking API.
No authentication required. Returns the top-ranked videos across all
categories from the Bilibili platform (Chinese video site).

Surface type: ranking
Bucket: hot_now
Locale: zh-Hans
"""

import logging
from typing import Optional, Tuple, List

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

# Bilibili ranking API endpoint
BILIBILI_RANKING_URL = "https://api.bilibili.com/x/web-interface/ranking/v2"

# Headers required to avoid being blocked (Referer is checked server-side)
DEFAULT_HEADERS = {
    "Referer": "https://www.bilibili.com",
    "User-Agent": "Mozilla/5.0",
}


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect trending videos from Bilibili ranking API.

    Config params (from TrendSurface.config_json):
        None required. API returns full list without pagination.

    Args:
        config: Surface configuration (no required params)
        cursor: Pagination cursor (unused — Bilibili returns full list)
        limit: Maximum items to collect

    Returns:
        (items, None) — no pagination cursor

    Raises:
        httpx.HTTPError: On API request failures
    """
    params = {
        "rid": 0,
        "type": "all",
    }

    logger.info("Fetching Bilibili trending videos...")

    async with RateLimitedClient(timeout=30.0) as client:
        response = await client.get(
            BILIBILI_RANKING_URL,
            params=params,
            headers=DEFAULT_HEADERS,
        )
        response.raise_for_status()
        data = response.json()

    # Bilibili returns {"code": 0, "data": {"list": [...]}}
    video_list = data.get("data", {}).get("list", [])

    items: List[CollectedItem] = []
    for rank, video in enumerate(video_list[:limit], start=1):
        bvid = video.get("bvid", "")
        aid = video.get("aid", "")
        title = video.get("title", "Untitled")
        desc = video.get("desc", "")
        pic = video.get("pic", "")

        # Build canonical video URL
        url = f"https://www.bilibili.com/video/{bvid}" if bvid else ""

        # Extract engagement signals from stat sub-object
        stat = video.get("stat", {})
        views = stat.get("view", 0)
        likes = stat.get("like", 0)
        coins = stat.get("coin", 0)
        shares = stat.get("share", 0)
        comments = stat.get("reply", 0)
        danmaku = stat.get("danmaku", 0)

        # Build raw_payload with complete video data + thumbnail helper key
        raw_payload = {
            **video,
            "_thumbnail_url": pic,
        }

        item: CollectedItem = {
            "external_id": bvid or str(aid),
            "title": title,
            "description": desc[:1000] if desc else None,
            "url": url,
            "locale": "zh-Hans",
            "rank_position": rank,
            "engagement_signals": {
                "views": views,
                "likes": likes,
                "comments": comments,
                "shares": shares,
                "coins": coins,
                "danmaku": danmaku,
            },
            "raw_payload": raw_payload,
        }
        items.append(item)

    logger.info(f"Collected {len(items)} items from Bilibili")
    return items, None
