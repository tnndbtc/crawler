"""
Bilibili Trending surface collector.

Fetches trending videos from Bilibili using the public ranking API.
No authentication required. Returns the top-ranked videos across all
categories from the Bilibili platform (Chinese video site).

Surface type: ranking
Bucket: hot_now
Locale: zh-Hans
"""

import asyncio
import logging
from typing import Optional, Tuple, List

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient
from shared.comment_settings import get_top_comments_count

logger = logging.getLogger(__name__)

# Bilibili ranking API endpoint
BILIBILI_RANKING_URL = "https://api.bilibili.com/x/web-interface/ranking/v2"

# Bilibili comment API — returns hot (most-liked) top-level replies for a video.
# type=1 = video; sort=1 = by likes (hot first); no authentication required.
BILIBILI_REPLY_URL = "https://api.bilibili.com/x/v2/reply/main"

# Maximum number of videos to attempt comment fetching on per run.
COMMENT_FETCH_POST_LIMIT = 20

# Headers required to avoid being blocked (Referer is checked server-side)
DEFAULT_HEADERS = {
    "Referer": "https://www.bilibili.com",
    "User-Agent": "Mozilla/5.0",
}


async def fetch_top_comments(
    client: RateLimitedClient,
    aid: int,
    limit: int,
) -> List[str]:
    """
    Fetch the top (most-liked) comments for a Bilibili video.

    Uses the public reply API — no authentication required for public videos.

    Args:
        client: HTTP client
        aid: Bilibili video numeric ID (av number)
        limit: Max number of comments to return

    Returns:
        List of comment text strings (up to 300 chars each), empty on failure
    """
    params = {
        "type": 1,    # 1 = video
        "oid": aid,
        "sort": 1,    # 1 = by likes (hot), 0 = by time
        "ps": limit,  # page size
    }
    try:
        response = await client.get(
            BILIBILI_REPLY_URL,
            params=params,
            headers=DEFAULT_HEADERS,
        )
        response.raise_for_status()
        data = response.json()

        replies = data.get("data", {}).get("replies") or []
        comments = []
        for reply in replies[:limit]:
            text = reply.get("content", {}).get("message", "").strip()
            if text:
                comments.append(text[:300])

        return comments

    except Exception as e:
        logger.debug(f"Could not fetch Bilibili comments for aid {aid}: {e}")
        return []


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect trending videos from Bilibili ranking API.

    Config params (from TrendSurface.config_json):
        - top_comments: Override number of comments to fetch per video (0 = disable).
                        Falls back to global SystemSettings[crawler.top_comments_per_post].

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

    # Resolve how many comments to fetch per video (0 = disabled)
    top_comments_count = await get_top_comments_count(config)

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

        # Pre-fetch comments for top-ranked videos concurrently (if enabled).
        # We do this inside the client context so the connection pool is reused.
        top_comments_map: dict = {}
        if top_comments_count > 0:
            candidates = [
                v for v in video_list[:COMMENT_FETCH_POST_LIMIT]
                if v.get("aid")
            ]
            sem = asyncio.Semaphore(3)

            async def _fetch(v: dict):
                async with sem:
                    aid_val = v["aid"]
                    comments = await fetch_top_comments(client, aid_val, top_comments_count)
                    if comments:
                        top_comments_map[str(aid_val)] = comments
                        logger.debug(
                            f"Fetched {len(comments)} comments for Bilibili aid {aid_val}"
                        )

            await asyncio.gather(*[_fetch(v) for v in candidates])

    items: List[CollectedItem] = []
    for rank, video in enumerate(video_list[:limit], start=1):
        bvid = video.get("bvid", "")
        aid = video.get("aid", "")
        title = video.get("title", "Untitled")
        desc = video.get("desc", "")
        pic = video.get("pic", "")
        # Bilibili API returns http:// URLs; force https:// to avoid
        # mixed-content browser blocks when the UI is served over HTTPS.
        if pic.startswith("http://"):
            pic = "https://" + pic[7:]

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

        # Build raw_payload with complete video data + thumbnail + top comments
        raw_payload = {
            **video,
            "_thumbnail_url": pic,
            "top_comments": top_comments_map.get(str(aid), []),
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
