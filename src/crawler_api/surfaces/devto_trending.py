"""
Dev.to Trending Articles surface collector.

Fetches trending articles from Dev.to (dev.to), a community of software
developers. Uses the public Dev.to API. An optional API key can be provided
to raise rate limits.

Surface type: ranking
Bucket: hot_now
Locale: en-US
"""

import logging
from typing import Optional, Tuple, List

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

# Dev.to articles API endpoint
DEVTO_ARTICLES_URL = "https://dev.to/api/articles"


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect trending articles from Dev.to using the public API.

    The `top=1` parameter returns articles sorted by popularity over the
    last day. No pagination is needed — the API returns the top N directly.

    Config params (from TrendSurface.config_json):
        - api_key: Optional Dev.to API key to increase rate limits.

    Args:
        config: Surface configuration
        cursor: Pagination cursor (unused — top articles returned in one call)
        limit: Maximum items to collect

    Returns:
        (items, None) — no pagination cursor

    Raises:
        httpx.HTTPError: On API request failures
    """
    api_key = config.get("api_key")

    params = {
        "top": 1,
        "per_page": limit,
    }

    headers = {}
    if api_key:
        headers["api-key"] = api_key

    logger.info(f"Fetching Dev.to trending articles (limit={limit})...")

    async with RateLimitedClient(timeout=30.0) as client:
        response = await client.get(
            DEVTO_ARTICLES_URL,
            params=params,
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()

    if not isinstance(data, list):
        logger.warning("Dev.to API returned unexpected format (expected list)")
        return [], None

    items: List[CollectedItem] = []
    for rank, article in enumerate(data[:limit], start=1):
        article_id = article.get("id")
        title = article.get("title", "Untitled")
        # Dev.to uses canonical_url as the article URL field
        url = article.get("url") or article.get("canonical_url", "")
        description = article.get("description", "")
        published_at = article.get("published_at")
        cover_image = article.get("cover_image")

        # Engagement signals
        upvotes = article.get("positive_reactions_count", 0)
        comments = article.get("comments_count", 0)

        # Build raw_payload and attach thumbnail helper key when available
        raw_payload = {**article}
        if cover_image:
            raw_payload["_thumbnail_url"] = cover_image

        item: CollectedItem = {
            "external_id": str(article_id) if article_id is not None else None,
            "title": title,
            "description": description[:1000] if description else None,
            "url": url,
            "locale": "en-US",
            "rank_position": rank,
            "engagement_signals": {
                "upvotes": upvotes,
                "comments": comments,
            },
            "raw_payload": raw_payload,
        }

        if published_at:
            item["published_at"] = published_at

        items.append(item)

    logger.info(f"Collected {len(items)} items from Dev.to")
    return items, None
