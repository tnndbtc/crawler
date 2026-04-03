"""
Weibo Sogou mirror surface collector.

Scrapes trending Weibo topics from Sogou's Weibo search mirror.
URL: https://weibo.sogou.com

No authentication required. Uses BeautifulSoup for HTML parsing.
Locale: zh-Hans
Surface type: ranking
Bucket: hot_now
"""

import logging
from typing import Optional, Tuple, List
from urllib.parse import quote

from bs4 import BeautifulSoup

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

SOGOU_WEIBO_URL = "https://weibo.sogou.com"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.sogou.com",
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
    Collect trending Weibo topics from Sogou Weibo mirror.

    Config params:
        None required.

    Args:
        config: Surface configuration (no required params)
        cursor: Pagination cursor (unused — no pagination)
        limit: Maximum items to collect

    Returns:
        (items, None) — no pagination cursor (gracefully returns 0 items on parse failure)
    """
    logger.info("Fetching Weibo trending topics via Sogou mirror...")

    async with RateLimitedClient(timeout=30.0) as client:
        response = await client.get(SOGOU_WEIBO_URL, headers=DEFAULT_HEADERS)
        response.raise_for_status()
        html = response.text

    soup = BeautifulSoup(html, "html.parser")
    items: List[CollectedItem] = []

    # Strategy 1: look for ranked list containers
    # Sogou Weibo typically shows a hot list in a <ul> or numbered <div> block
    list_items = soup.select("ul.hot-list li, ul.trend-list li, div.hot-list-item")

    if not list_items:
        # Strategy 2: any <li> inside a container that looks like a hot/rank list
        for ul in soup.find_all("ul"):
            classes = " ".join(ul.get("class", []))
            if any(kw in classes.lower() for kw in ("hot", "rank", "trend", "top")):
                list_items = ul.find_all("li")
                if list_items:
                    break

    if not list_items:
        # Strategy 3: find numbered items — spans/divs that start with digits
        numbered = []
        for el in soup.find_all(["li", "div", "tr"]):
            text = el.get_text(strip=True)
            # Match elements that start with a rank number like "1.", "2 ", etc.
            if text and len(text) > 2 and text[0].isdigit():
                numbered.append(el)
        list_items = numbered[:limit] if numbered else []

    if not list_items:
        logger.warning(
            "Weibo Sogou: no items parsed from page — structure may have changed"
        )
        return [], None

    seen_titles: set = set()
    rank = 1

    for el in list_items:
        if rank > limit:
            break

        # Extract anchor (may be direct link to topic or absent)
        anchor = el.select_one("a[href]")
        title_el = anchor or el

        title = title_el.get_text(strip=True)
        # Strip leading rank numbers like "1 ", "1."
        if title and title[0].isdigit():
            title = title.lstrip("0123456789. ").strip()

        if not title or title in seen_titles or len(title) < 2:
            continue
        seen_titles.add(title)

        # Build URL
        if anchor and anchor.get("href", "").startswith("http"):
            url = anchor["href"]
        else:
            url = _build_weibo_search_url(title)

        # Try to extract a hot score number from the element text
        hot_score_str = ""
        score_el = el.select_one("span.num, span.hot, span[class*='score'], em")
        if score_el:
            hot_score_str = score_el.get_text(strip=True)

        engagement: dict = {}
        if hot_score_str:
            try:
                clean = hot_score_str.replace(",", "").replace("万", "0000").replace("亿", "00000000")
                clean = "".join(c for c in clean if c.isdigit())
                engagement["hot_score"] = int(clean) if clean else 0
            except (ValueError, TypeError):
                pass

        item: CollectedItem = {
            "title": title,
            "url": url,
            "locale": "zh-Hans",
            "rank_position": rank,
            "engagement_signals": engagement,
            "raw_payload": {
                "title": title,
                "url": url,
                "hot_score_raw": hot_score_str,
                "rank": rank,
                "source": "weibo_sogou",
            },
        }
        items.append(item)
        rank += 1

    if not items:
        logger.warning(
            "Weibo Sogou: 0 items after parsing — page structure may have changed"
        )

    logger.info(f"Collected {len(items)} items from Weibo Sogou")
    return items, None
