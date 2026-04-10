"""
Zhihu Hot (知乎热榜) surface collector.

Uses Zhihu mobile API (no auth required).
API: https://api.zhihu.com/topstory/hot-list

Returns 30 items with title, question ID, excerpt, and hot score.
Locale: zh-Hans
Surface type: ranking
Bucket: hot_now
"""

import logging
from typing import Optional, Tuple, List

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

ZHIHU_API_URL = "https://api.zhihu.com/topstory/hot-list"

DEFAULT_HEADERS = {
    "User-Agent": (
        "com.zhihu.android/9.16.0 (Android 12; Pixel 6)"
    ),
    "Accept": "application/json",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def _parse_hot_score(text: str) -> int:
    """
    Parse Zhihu hot score string to integer.

    Examples:
        "1234万热度" → 12340000
        "567热度"    → 567
        "8.9亿热度"  → 890000000
    """
    if not text:
        return 0
    text = text.strip().replace("热度", "").replace(",", "").strip()
    try:
        if "亿" in text:
            num = float(text.replace("亿", "")) * 100_000_000
            return int(num)
        elif "万" in text:
            num = float(text.replace("万", "")) * 10_000
            return int(num)
        else:
            digits = "".join(c for c in text if c.isdigit() or c == ".")
            return int(float(digits)) if digits else 0
    except (ValueError, TypeError):
        return 0


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect trending hot questions from Zhihu mobile API.

    Config params:
        None required.

    Args:
        config: Surface configuration (no required params)
        cursor: Pagination cursor (unused — no pagination)
        limit: Maximum items to collect

    Returns:
        (items, None) — no pagination cursor
    """
    logger.info("Fetching Zhihu Hot via mobile API...")

    async with RateLimitedClient(timeout=30.0) as client:
        response = await client.get(ZHIHU_API_URL, headers=DEFAULT_HEADERS)
        response.raise_for_status()
        data = response.json()

    items: List[CollectedItem] = []

    for rank, entry in enumerate(data.get("data", [])[:limit], start=1):
        target = entry.get("target", {})  # defensive — some items may differ

        title = target.get("title", "")
        question_id = target.get("id")

        if not title or not question_id:
            logger.debug(f"Skipping malformed Zhihu entry at rank {rank}")
            continue

        url = f"https://www.zhihu.com/question/{question_id}"
        excerpt = target.get("excerpt", "")
        detail_text = entry.get("detail_text", "")  # e.g. "1234万热度"
        hot_score = _parse_hot_score(detail_text)

        item: CollectedItem = {
            "external_id": f"zhihu_{question_id}",
            "title": title,
            "url": url,
            "locale": "zh-Hans",
            "rank_position": rank,
            "engagement_signals": {"hot_score": hot_score} if hot_score else {},
            "raw_payload": entry,
        }

        if excerpt:
            item["description"] = excerpt

        items.append(item)

    if not items:
        logger.warning("Zhihu Hot API returned 0 usable items")
    else:
        logger.info(f"Collected {len(items)} items from Zhihu Hot API")

    return items, None
