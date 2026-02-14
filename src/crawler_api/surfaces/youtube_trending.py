"""
YouTube Trending surface collector (STUB for testing).

In production, this would use YouTube Data API.
For now, returns mock data following the collector interface.

Surface type: ranking
Bucket: category_entertainment
"""

import asyncio
from typing import Optional, Tuple, List
from datetime import datetime, timezone, timedelta

from .collector_interface import CollectedItem


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect trending videos from YouTube.

    STUB IMPLEMENTATION - Returns mock data for testing.

    Config params (from TrendSurface.config_json):
        - category: Category to collect from (default: "trending")
        - region_code: YouTube region code (default: "US")

    Args:
        config: Surface configuration
        cursor: Pagination cursor (page token in production)
        limit: Maximum items to collect

    Returns:
        (items, next_cursor)
    """
    # Simulate API delay
    await asyncio.sleep(0.7)

    category = config.get('category', 'trending')
    region_code = config.get('region_code', 'US')
    actual_limit = min(limit, 50)  # YouTube API typically returns max 50

    items: List[CollectedItem] = []

    # Generate mock items
    for i in range(1, actual_limit + 1):
        # Simulate videos published at different times
        published_at = datetime.now(timezone.utc) - timedelta(hours=i)

        item: CollectedItem = {
            "external_id": f"yt_stub_{i}",
            "title": f"[STUB] Trending Video #{i} - {category.upper()}",
            "description": f"This is a stub YouTube video description. Would contain actual video description in production.",
            "url": f"https://youtube.com/watch?v=STUB{i:05d}",
            "locale": "en-US",
            "published_at": published_at.isoformat(),

            # CRITICAL: rank_position (from /tmp/t9)
            "rank_position": i,

            # CRITICAL: engagement_signals (from /tmp/t9)
            "engagement_signals": {
                "views": 1000000 - (i * 10000),  # Decreasing by rank
                "upvotes": 50000 - (i * 500),    # Likes
                "comments": 2000 - (i * 20),
            },

            # CRITICAL: raw_payload (from /tmp/t9)
            "raw_payload": {
                "stub": True,
                "video_id": f"STUB{i:05d}",
                "channel_title": f"Stub Channel {i}",
                "category_id": "10",  # Music
                "published_at": published_at.isoformat(),
                "statistics": {
                    "viewCount": str(1000000 - (i * 10000)),
                    "likeCount": str(50000 - (i * 500)),
                    "commentCount": str(2000 - (i * 20)),
                },
                "region_code": region_code,
            }
        }
        items.append(item)

    # No pagination in stub
    next_cursor = None

    return items, next_cursor
