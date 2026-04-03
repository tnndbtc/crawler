"""
Weibo Hot Search (微博热搜) surface collector.

Uses Weibo's internal AJAX endpoint (no login required) that the official
web frontend calls to populate the hot-search sidebar.
URL: https://weibo.com/ajax/side/hotSearch

Surface type: ranking
Bucket: hot_now
Locale: zh-Hans
"""

import logging
from typing import Optional, Tuple, List
from urllib.parse import quote

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

WEIBO_HOT_JSON_URL = "https://weibo.com/ajax/side/hotSearch"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://weibo.com/",
    "X-Requested-With": "XMLHttpRequest",
}


def _build_weibo_search_url(topic: str) -> str:
    """Construct a Weibo search URL for the given topic."""
    return f"https://s.weibo.com/weibo?q={quote(topic)}"


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect trending hot search topics from Weibo via the AJAX JSON API.

    This endpoint is called by Weibo's own web frontend for the hot-search
    sidebar and does not require authentication.

    Config params:
        None required.

    Args:
        config: Surface configuration (no required params)
        cursor: Pagination cursor (unused — no pagination)
        limit: Maximum items to collect

    Returns:
        (items, None) — no pagination cursor

    Raises:
        httpx.HTTPError: On HTTP request failures
        RuntimeError: If the response is not parseable
    """
    logger.info("Fetching Weibo Hot Search via AJAX JSON API...")

    async with RateLimitedClient(timeout=30.0) as client:
        response = await client.get(WEIBO_HOT_JSON_URL, headers=DEFAULT_HEADERS)
        response.raise_for_status()
        data = response.json()

    # Response schema:
    # {
    #   "ok": 1,
    #   "data": {
    #     "realtime": [
    #       {"word": "话题名称", "num": 1234567, "category": "热", "label_name": "热", ...},
    #       ...
    #     ],
    #     "hotgov": {"word": "...", ...}   # pinned government topic (rank 0 / ad)
    #   }
    # }

    if data.get("ok") != 1:
        raise RuntimeError(
            f"Weibo Hot AJAX API returned ok={data.get('ok')!r} — "
            "API may have changed or be temporarily unavailable"
        )

    realtime = data.get("data", {}).get("realtime", [])
    if not realtime:
        raise RuntimeError(
            "Weibo Hot AJAX API: 'realtime' list is empty — "
            "API response structure may have changed"
        )

    items: List[CollectedItem] = []
    for rank, entry in enumerate(realtime[:limit], start=1):
        word = entry.get("word", "").strip()
        if not word:
            continue

        # Some entries are pinned ads/government topics — still include them
        hot_num = entry.get("num", 0)
        try:
            hot_score = int(hot_num)
        except (TypeError, ValueError):
            hot_score = 0

        label = entry.get("label_name", "") or entry.get("category", "")
        url = _build_weibo_search_url(word)

        item: CollectedItem = {
            "title": word,
            "url": url,
            "locale": "zh-Hans",
            "rank_position": rank,
            "engagement_signals": {"hot_score": hot_score} if hot_score else {},
            "raw_payload": {
                "title": word,
                "url": url,
                "hot_score": hot_score,
                "label": label,
                "rank": rank,
                "source": "weibo_hot",
            },
        }
        items.append(item)

    logger.info(f"Collected {len(items)} items from Weibo Hot Search")
    return items, None
