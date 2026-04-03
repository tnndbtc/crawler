"""
Zhihu Hot (知乎热榜) surface collector.

Scrapes the Zhihu hot questions list.
URL: https://www.zhihu.com/hot

No authentication required, but Zhihu may show a login popup.
Uses BeautifulSoup for HTML parsing.
Locale: zh-Hans
Surface type: ranking
Bucket: hot_now
"""

import logging
from typing import Optional, Tuple, List

from bs4 import BeautifulSoup

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

ZHIHU_HOT_URL = "https://www.zhihu.com/hot"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.zhihu.com",
    "X-Requested-With": "XMLHttpRequest",
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
    Collect trending hot questions from Zhihu Hot list.

    Config params:
        None required.

    Args:
        config: Surface configuration (no required params)
        cursor: Pagination cursor (unused — no pagination)
        limit: Maximum items to collect

    Returns:
        (items, None) — no pagination cursor (returns 0 items gracefully on login block)
    """
    logger.info("Fetching Zhihu Hot trending questions...")

    async with RateLimitedClient(timeout=30.0) as client:
        response = await client.get(ZHIHU_HOT_URL, headers=DEFAULT_HEADERS)
        response.raise_for_status()
        html = response.text

    soup = BeautifulSoup(html, "html.parser")
    items: List[CollectedItem] = []

    # Strategy 1: <section> or <div> with class containing "HotItem"
    hot_items = soup.select("section.HotItem, div.HotItem, section[class*='HotItem'], div[class*='HotItem']")

    if not hot_items:
        # Strategy 2: any container that holds hot list items
        hot_items = soup.select(".HotList-item, .hot-item, li.HotItem")

    if not hot_items:
        # Strategy 3: look for <a href="/question/..."> anchors
        question_anchors = soup.select('a[href*="/question/"]')
        # De-duplicate by href
        seen_hrefs: set = set()
        rank = 1
        for anchor in question_anchors:
            if rank > limit:
                break
            href = anchor.get("href", "")
            if not href or href in seen_hrefs:
                continue
            seen_hrefs.add(href)

            title = anchor.get_text(strip=True)
            if not title or len(title) < 4:
                continue

            if href.startswith("/"):
                url = f"https://www.zhihu.com{href}"
            elif href.startswith("http"):
                url = href
            else:
                continue

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
                    "source": "zhihu_hot",
                    "_selector": "question_anchor_fallback",
                },
            }
            items.append(item)
            rank += 1

        if not items:
            logger.warning(
                "Zhihu Hot: 0 items parsed — Zhihu may be showing a login popup "
                "or the page structure has changed"
            )
        else:
            logger.info(f"Collected {len(items)} items from Zhihu Hot (fallback selector)")

        return items, None

    # Parse from HotItem containers
    for rank, section in enumerate(hot_items[:limit], start=1):
        # Title: <h2> inside the item
        h2 = section.select_one("h2")
        title = h2.get_text(strip=True) if h2 else ""

        if not title:
            # Try any prominent text element
            title_el = section.select_one("a")
            title = title_el.get_text(strip=True) if title_el else ""

        if not title:
            continue

        # URL: <a href="/question/{id}">
        link_el = section.select_one('a[href*="/question/"]')
        if link_el:
            href = link_el.get("href", "")
            if href.startswith("/"):
                url = f"https://www.zhihu.com{href}"
            else:
                url = href
        else:
            url = ZHIHU_HOT_URL

        # Description: <p> text snippet
        desc_el = section.select_one("p")
        description = desc_el.get_text(strip=True) if desc_el else None

        # Hot score: span containing "热度" or matching "万热度"
        hot_score_str = ""
        hot_score = 0
        for span in section.select("span"):
            text = span.get_text(strip=True)
            if "热度" in text or "万" in text:
                hot_score_str = text
                hot_score = _parse_hot_score(text)
                break

        item: CollectedItem = {
            "title": title,
            "url": url,
            "locale": "zh-Hans",
            "rank_position": rank,
            "engagement_signals": {"hot_score": hot_score} if hot_score else {},
            "raw_payload": {
                "title": title,
                "url": url,
                "hot_score_raw": hot_score_str,
                "hot_score": hot_score,
                "description": description,
                "rank": rank,
                "source": "zhihu_hot",
            },
        }
        if description:
            item["description"] = description

        items.append(item)

    if not items:
        logger.warning(
            "Zhihu Hot: 0 items parsed — Zhihu may be showing a login popup "
            "or the page structure has changed"
        )

    logger.info(f"Collected {len(items)} items from Zhihu Hot")
    return items, None
