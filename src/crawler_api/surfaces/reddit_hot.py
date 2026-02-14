"""
Reddit Hot surface collector (STUB for testing).

In production, this would use Reddit API or web scraping.
For now, returns mock data following the collector interface.

Surface type: ranking
Bucket: hot_now (major trending content)
"""

import asyncio
from typing import Optional, Tuple, List
from datetime import datetime, timezone

from .collector_interface import CollectedItem


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect hot posts from Reddit.

    STUB IMPLEMENTATION - Returns mock data for testing.

    Config params (from TrendSurface.config_json):
        - subreddit: Subreddit to collect from (default: "all")
        - limit: Max items (default: from function param)

    Args:
        config: Surface configuration
        cursor: Pagination cursor (unused in stub)
        limit: Maximum items to collect

    Returns:
        (items, next_cursor)
    """
    # Simulate API delay
    await asyncio.sleep(0.5)

    subreddit = config.get('subreddit', 'all')
    actual_limit = min(limit, 100)  # Cap at 100

    items: List[CollectedItem] = []

    # Generate mock items
    for i in range(1, actual_limit + 1):
        item: CollectedItem = {
            "external_id": f"reddit_stub_{i}",
            "title": f"[STUB] Hot Reddit Post #{i} from r/{subreddit}",
            "description": f"This is a stub description for testing. In production, this would be the actual post content.",
            "url": f"https://reddit.com/r/{subreddit}/comments/stub{i}",
            "locale": "en-US",
            "published_at": datetime.now(timezone.utc).isoformat(),

            # CRITICAL: rank_position (from /tmp/t9)
            "rank_position": i,

            # CRITICAL: engagement_signals (from /tmp/t9)
            "engagement_signals": {
                "upvotes": 10000 - (i * 100),  # Decreasing by rank
                "comments": 500 - (i * 5),
            },

            # CRITICAL: raw_payload (from /tmp/t9)
            "raw_payload": {
                "stub": True,
                "id": f"stub_{i}",
                "subreddit": subreddit,
                "author": "stub_user",
                "created_utc": datetime.now(timezone.utc).timestamp(),
                "score": 10000 - (i * 100),
                "num_comments": 500 - (i * 5),
            }
        }
        items.append(item)

    # No pagination in stub
    next_cursor = None

    return items, next_cursor
