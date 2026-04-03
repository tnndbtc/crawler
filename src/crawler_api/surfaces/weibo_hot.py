"""
Weibo Hot Search (微博热搜) surface collector.

Scrapes the Weibo hot search summary page with human-behavior simulation
to reduce bot-detection probability.
URL: https://s.weibo.com/top/summary

Uses HumanBrowsingSession for realistic request patterns.
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
from shared.human_behavior import HumanBrowsingSession
from shared.url_utils import extract_domain

logger = logging.getLogger(__name__)

WEIBO_HOT_URL = "https://s.weibo.com/top/summary"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://weibo.com",
}


def _build_weibo_search_url(topic: str) -> str:
    """Construct a Weibo search URL for the given topic."""
    return f"https://s.weibo.com/weibo?q={quote(topic)}"


def _parse_hot_score(text: str) -> int:
    """Parse a Weibo heat index string to int."""
    if not text:
        return 0
    clean = text.strip().replace(",", "")
    try:
        # Strip non-digit suffix
        digits = "".join(c for c in clean if c.isdigit())
        return int(digits) if digits else 0
    except (ValueError, TypeError):
        return 0


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect trending hot search topics from Weibo.

    Uses HumanBrowsingSession to simulate realistic browsing behavior.

    Config params:
        None required.

    Args:
        config: Surface configuration (no required params)
        cursor: Pagination cursor (unused — no pagination)
        limit: Maximum items to collect

    Returns:
        (items, None) — no pagination cursor
    """
    logger.info("Fetching Weibo Hot Search trending topics...")

    domain = extract_domain(WEIBO_HOT_URL)

    async with RateLimitedClient(timeout=30.0) as client:
        # Simulate human browsing: optional homepage visit before the target page
        session = await HumanBrowsingSession.from_config(client, domain)
        await session.maybe_visit_homepage(WEIBO_HOT_URL)

        response = await client.get(WEIBO_HOT_URL, headers=DEFAULT_HEADERS)
        response.raise_for_status()
        html = response.text

    soup = BeautifulSoup(html, "html.parser")
    items: List[CollectedItem] = []

    # Strategy 1: hot search table rows (<tr> with rank, topic, score)
    rows = soup.select("table tr")
    parsed_from_table = False

    if rows:
        rank = 1
        for row in rows:
            if rank > limit:
                break
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            # Typical structure: [rank_num | topic_link | hot_score | ...]
            title = None
            url = None
            hot_score_str = ""

            for cell in cells:
                anchor = cell.select_one("a[href]")
                if anchor and not title:
                    title = anchor.get_text(strip=True)
                    href = anchor["href"]
                    if href.startswith("http"):
                        url = href
                    elif href.startswith("/"):
                        url = f"https://s.weibo.com{href}"

            # Hot score — last cell with digits only
            if cells:
                last_cell_text = cells[-1].get_text(strip=True)
                if last_cell_text and last_cell_text.isdigit():
                    hot_score_str = last_cell_text

            if not title:
                continue
            if not url:
                url = _build_weibo_search_url(title)

            hot_score = _parse_hot_score(hot_score_str)
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
                    "rank": rank,
                    "source": "weibo_hot",
                },
            }
            items.append(item)
            rank += 1
            parsed_from_table = True

    if not parsed_from_table or not items:
        # Strategy 2: HotItem anchor elements
        hot_anchors = soup.select("a.HotItem-content, div.HotItem a")
        if hot_anchors:
            items = []
            for rank, anchor in enumerate(hot_anchors[:limit], start=1):
                title = anchor.get_text(strip=True)
                if not title:
                    continue
                href = anchor.get("href", "")
                if href.startswith("http"):
                    url = href
                elif href.startswith("/"):
                    url = f"https://s.weibo.com{href}"
                else:
                    url = _build_weibo_search_url(title)

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
                        "source": "weibo_hot",
                        "_selector": "HotItem-content",
                    },
                }
                items.append(item)

    if not items:
        logger.warning(
            "Weibo Hot: 0 items parsed from page — "
            "page may require login or structure changed"
        )

    logger.info(f"Collected {len(items)} items from Weibo Hot Search")
    return items, None
