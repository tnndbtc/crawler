"""
Lobste.rs Hottest Stories surface collector.

Fetches the hottest stories from Lobste.rs, a technology-focused link
aggregation community with a curated member base. No authentication required.

Surface type: ranking
Bucket: hot_now
Locale: en-US
"""

import logging
from typing import Optional, Tuple, List

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

# Lobste.rs hottest stories API endpoint
LOBSTERS_HOTTEST_URL = "https://lobste.rs/hottest.json"


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect hottest stories from Lobste.rs using the public JSON API.

    Config params (from TrendSurface.config_json):
        None required.

    Args:
        config: Surface configuration (no required params)
        cursor: Pagination cursor (unused — API returns full list)
        limit: Maximum items to collect

    Returns:
        (items, None) — no pagination cursor

    Raises:
        httpx.HTTPError: On API request failures
    """
    logger.info("Fetching Lobste.rs hottest stories...")

    async with RateLimitedClient(timeout=30.0) as client:
        response = await client.get(LOBSTERS_HOTTEST_URL)
        response.raise_for_status()
        data = response.json()

    if not isinstance(data, list):
        logger.warning("Lobste.rs API returned unexpected format (expected list)")
        return [], None

    items: List[CollectedItem] = []
    for rank, story in enumerate(data[:limit], start=1):
        short_id = story.get("short_id", "")
        title = story.get("title", "Untitled")
        comments_url = story.get("comments_url", "")
        created_at = story.get("created_at")

        # Use linked article URL; fall back to the Lobste.rs comments page
        story_url = story.get("url") or comments_url

        score = story.get("score", 0)
        upvotes = story.get("upvotes", 0)
        downvotes = story.get("downvotes", 0)
        comment_count = story.get("comment_count", 0)
        tags = story.get("tags", [])

        # Extract submitter username
        submitter = story.get("submitter_user", {})
        submitter_username = submitter.get("username", "") if isinstance(submitter, dict) else ""

        item: CollectedItem = {
            "external_id": short_id,
            "title": title,
            "url": story_url,
            "locale": "en-US",
            "rank_position": rank,
            "engagement_signals": {
                "score": score,
                "upvotes": upvotes,
                "comments": comment_count,
            },
            "raw_payload": {
                **story,
                "_metadata": {
                    "submitter_username": submitter_username,
                    "tags": tags,
                    "comments_url": comments_url,
                    "downvotes": downvotes,
                },
            },
        }

        if created_at:
            item["published_at"] = created_at

        items.append(item)

    logger.info(f"Collected {len(items)} items from Lobste.rs")
    return items, None
