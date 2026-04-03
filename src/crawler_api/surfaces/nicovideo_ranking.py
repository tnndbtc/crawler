"""
NicoNico Video Ranking surface collector.

Fetches the ranking of videos from NicoNico (nicovideo.jp) using the
nvapi ranking endpoint. Requires the x-frontend-id header to receive
a valid JSON response.

Surface type: ranking
Bucket: hot_now
Locale: ja-JP
"""

import logging
from typing import Optional, Tuple, List

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

# NicoNico ranking API endpoint
NICOVIDEO_RANKING_URL = "https://nvapi.nicovideo.jp/v1/ranking/genre/all"

# Required headers — x-frontend-id=6 signals the web frontend
NICOVIDEO_HEADERS = {
    "Accept": "application/json",
    "x-frontend-id": "6",
}


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect ranked videos from NicoNico (nicovideo.jp).

    Config params (from TrendSurface.config_json):
        None required. Fetches the all-genre 24-hour ranking.

    Args:
        config: Surface configuration (no required params)
        cursor: Pagination cursor (unused — API returns full ranking list)
        limit: Maximum items to collect

    Returns:
        (items, None) — no pagination cursor

    Raises:
        httpx.HTTPError: On API request failures
    """
    params = {
        "term": "24h",
    }

    logger.info("Fetching NicoNico video ranking...")

    async with RateLimitedClient(timeout=30.0) as client:
        response = await client.get(
            NICOVIDEO_RANKING_URL,
            params=params,
            headers=NICOVIDEO_HEADERS,
        )
        response.raise_for_status()
        data = response.json()

    # API returns {"data": {"items": [...]}}
    video_items = data.get("data", {}).get("items", [])

    items: List[CollectedItem] = []
    for rank, video in enumerate(video_items[:limit], start=1):
        video_id = video.get("id", "")
        title = video.get("title", "Untitled")
        registered_at = video.get("registeredAt")

        # Build canonical NicoNico watch URL
        url = f"https://www.nicovideo.jp/watch/{video_id}" if video_id else ""

        # Engagement signals live under "count" sub-object
        count = video.get("count", {})
        views = count.get("view", 0)
        comments = count.get("comment", 0)
        mylist = count.get("mylist", 0)
        likes = count.get("like", 0)

        # Thumbnail URL lives under "thumbnail" sub-object
        thumbnail = video.get("thumbnail", {})
        thumbnail_url = thumbnail.get("url", "") if isinstance(thumbnail, dict) else ""

        raw_payload = {
            **video,
            "_thumbnail_url": thumbnail_url,
        }

        item: CollectedItem = {
            "external_id": str(video_id) if video_id else None,
            "title": title,
            "url": url,
            "locale": "ja-JP",
            "rank_position": rank,
            "engagement_signals": {
                "views": views,
                "comments": comments,
                "likes": likes,
                "mylist": mylist,
            },
            "raw_payload": raw_payload,
        }

        if registered_at:
            item["published_at"] = registered_at

        items.append(item)

    logger.info(f"Collected {len(items)} items from NicoNico")
    return items, None
