"""
Baidu Hot (百度热搜) surface collector.

Scrapes the real-time trending hot search list from Baidu's trending board.
URL: https://top.baidu.com/board?tab=realtime

No authentication required. Uses BeautifulSoup for HTML parsing.
Locale: zh-Hans
Surface type: ranking
Bucket: hot_now
"""

import logging
from typing import Optional, Tuple, List
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

BAIDU_HOT_URL = "https://top.baidu.com/board?tab=realtime"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.baidu.com",
}


def _parse_hot_score(score_str: str) -> int:
    """
    Parse Baidu hot score string to integer.

    Examples:
        "3856万" → 38560000
        "1.2亿" → 120000000
        "56789" → 56789
        "56789热度" → 56789
    """
    if not score_str:
        return 0

    text = score_str.strip().replace(",", "").replace("热度", "").replace("搜索", "").strip()

    try:
        if "亿" in text:
            num = float(text.replace("亿", "")) * 100_000_000
            return int(num)
        elif "万" in text:
            num = float(text.replace("万", "")) * 10_000
            return int(num)
        else:
            # Strip any trailing non-numeric chars
            clean = "".join(c for c in text if c.isdigit() or c == ".")
            return int(float(clean)) if clean else 0
    except (ValueError, TypeError):
        return 0


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect real-time trending topics from Baidu Hot Search board.

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
        RuntimeError: If no items could be parsed from the page
    """
    logger.info("Fetching Baidu Hot trending board...")

    async with RateLimitedClient(timeout=30.0) as client:
        response = await client.get(BAIDU_HOT_URL, headers=DEFAULT_HEADERS)
        response.raise_for_status()
        html = response.text

    soup = BeautifulSoup(html, "html.parser")
    items: List[CollectedItem] = []

    # Strategy 1: primary selector — each trending item wrapper
    wrappers = soup.select("div.category-wrap_iQLTo")
    if not wrappers:
        # Strategy 2: alternate item wrapper
        wrappers = soup.select("div.item-wrap_J0Tdd")

    if wrappers:
        for rank, wrapper in enumerate(wrappers[:limit], start=1):
            # Extract title
            title_el = wrapper.select_one("div.c-single-text-ellipsis") or wrapper.select_one("a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            if not title:
                continue

            # Extract link
            link_el = wrapper.select_one("a[href]")
            url = link_el["href"] if link_el and link_el.get("href") else BAIDU_HOT_URL
            if url.startswith("/"):
                url = urljoin("https://top.baidu.com", url)

            # Extract hot score
            score_el = wrapper.select_one("div.hot-index_1Bl1a") or wrapper.select_one(".trend_2RttY")
            hot_score_str = score_el.get_text(strip=True) if score_el else ""
            hot_score = _parse_hot_score(hot_score_str)

            item: CollectedItem = {
                "title": title,
                "url": url,
                "locale": "zh-Hans",
                "rank_position": rank,
                "engagement_signals": {
                    "hot_score": hot_score,
                },
                "raw_payload": {
                    "title": title,
                    "url": url,
                    "hot_score_raw": hot_score_str,
                    "hot_score": hot_score,
                    "rank": rank,
                    "source": "baidu_hot",
                },
            }
            items.append(item)
    else:
        # Strategy 3: fallback — find any ranking list with <a> tags
        ranking_divs = soup.select("div[class*='list'] a, div[class*='rank'] a, ol a, ul a")
        seen_titles: set = set()
        rank = 1
        for anchor in ranking_divs:
            title = anchor.get_text(strip=True)
            if not title or title in seen_titles or len(title) < 2:
                continue
            seen_titles.add(title)

            url = anchor.get("href", BAIDU_HOT_URL)
            if url.startswith("/"):
                url = urljoin("https://top.baidu.com", url)

            item: CollectedItem = {
                "title": title,
                "url": url,
                "locale": "zh-Hans",
                "rank_position": rank,
                "engagement_signals": {},
                "raw_payload": {
                    "title": title,
                    "url": url,
                    "rank": rank,
                    "source": "baidu_hot",
                    "_fallback_selector": True,
                },
            }
            items.append(item)
            rank += 1
            if rank > limit:
                break

    if not items:
        raise RuntimeError(
            "Baidu Hot: no items parsed — page structure may have changed"
        )

    logger.info(f"Collected {len(items)} items from Baidu Hot")
    return items, None
