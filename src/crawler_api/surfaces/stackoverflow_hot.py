"""
Stack Overflow Hot Questions surface collector.

Fetches hot questions from Stack Overflow (or other Stack Exchange sites) via
the public Stack Exchange API v2.3. No API key required for anonymous access
(300 requests/day quota, sufficient at poll_interval_seconds=10800 → ~8 req/day).

API endpoint:
  https://api.stackexchange.com/2.3/questions?order=desc&sort=hot&site=stackoverflow&pagesize=30

WARNING: Anonymous quota is 300 req/day shared across all instances.
  At poll_interval_seconds=10800 this surface uses ~8 req/day.
  If adding more Stack Exchange sites (superuser, serverfault), register a free
  API key and add it to config_json["key"] to raise limit to 10,000 req/day.

Surface type: ranking
Bucket: category_tech
Locale: en-US
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple, List

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

STACKEXCHANGE_API_URL = "https://api.stackexchange.com/2.3/questions"


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect hot questions from Stack Overflow via Stack Exchange API v2.3.

    Response is gzip-compressed; httpx decompresses automatically.
    Items include title, link, score, view_count, and tags array.

    Config params (from TrendSurface.config_json):
        site (str): Stack Exchange site slug, default "stackoverflow".
        key (str): Optional API key to increase rate limit from 300 to 10,000/day.

    Args:
        config: Surface configuration
        cursor: Pagination cursor (unused — top 30 is sufficient)
        limit: Maximum items to collect

    Returns:
        (items, None) — no pagination cursor

    Raises:
        httpx.HTTPError: On API request failures
    """
    site = config.get("site", "stackoverflow")
    api_key = config.get("key")
    locale = config.get("locale", "en-US")

    params = {
        "order": "desc",
        "sort": "hot",
        "site": site,
        "pagesize": min(limit, 30),
        "filter": "default",
    }
    if api_key:
        params["key"] = api_key

    logger.info(f"Fetching Stack Overflow hot questions (site={site})...")

    async with RateLimitedClient(timeout=30.0) as client:
        response = await client.get(STACKEXCHANGE_API_URL, params=params)
        response.raise_for_status()
        data = response.json()

    question_items = data.get("items", [])
    items: List[CollectedItem] = []

    for rank, question in enumerate(question_items[:limit], start=1):
        question_id = question.get("question_id", "")
        title = question.get("title", "Untitled")
        url = question.get("link", "")
        score = question.get("score", 0)
        view_count = question.get("view_count", 0)
        tags = question.get("tags", [])
        creation_date = question.get("creation_date")

        published_at = (
            datetime.fromtimestamp(creation_date, tz=timezone.utc).isoformat()
            if creation_date
            else datetime.now(timezone.utc).isoformat()
        )

        items.append({
            "external_id": str(question_id) if question_id else None,
            "title": title,
            "url": url,
            "locale": locale,
            "published_at": published_at,
            "rank_position": rank,
            "engagement_signals": {
                "score": score,
                "views": view_count,
            },
            "raw_payload": {
                **question,
                "tags": tags,
                "site": site,
                "source": "stackoverflow_hot",
            },
        })

    logger.info(f"Collected {len(items)} hot questions from Stack Overflow")
    return items, None
