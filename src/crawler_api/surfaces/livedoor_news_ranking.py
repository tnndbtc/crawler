"""
Livedoor News Ranking surface collector.

Fetches news ranking from LINE's livedoor.com, a major Japanese content portal.
The ranking page is fully public, no login required.

URL: https://news.livedoor.com/ranking/

Surface type: ranking
Bucket: rising
Locale: ja-JP
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple, List
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

LIVEDOOR_RANKING_URL = "https://news.livedoor.com/ranking/"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://news.livedoor.com/",
}


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect news ranking from Livedoor News (news.livedoor.com).

    Scrapes the public ranking page. No authentication required.
    Primary selectors (in order of preference):
      1. ul.rankList > li
      2. div.rankWrap ol > li

    Config params (from TrendSurface.config_json):
        None required.

    Args:
        config: Surface configuration (no required params)
        cursor: Pagination cursor (unused — single page)
        limit: Maximum items to collect

    Returns:
        (items, None) — no pagination cursor

    Raises:
        httpx.HTTPError: On request failures
    """
    logger.info("Fetching Livedoor News ranking...")

    async with RateLimitedClient(timeout=30.0) as client:
        response = await client.get(LIVEDOOR_RANKING_URL, headers=DEFAULT_HEADERS)
        response.raise_for_status()
        html = response.text

    soup = BeautifulSoup(html, "html.parser")
    items: List[CollectedItem] = []
    seen_urls: set = set()
    rank = 0

    # Primary selector 1: ul.rankList > li
    list_items = soup.select("ul.rankList > li")

    # Primary selector 2: div.rankWrap ol > li
    if not list_items:
        list_items = soup.select("div.rankWrap ol > li")

    if list_items:
        for li in list_items:
            if rank >= limit:
                break
            a_tag = li.select_one("a")
            if not a_tag:
                continue
            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")
            if not href or not title or len(title) < 3:
                continue
            url = urljoin("https://news.livedoor.com", href)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            rank += 1
            items.append(_build_item(rank, title, url))

    # Fallback: any article links
    if not items:
        logger.warning("Livedoor primary selectors found nothing — using fallback")
        for a_tag in soup.select('a[href*="/article/"]'):
            if rank >= limit:
                break
            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")
            if not href or not title or len(title) < 5:
                continue
            url = urljoin("https://news.livedoor.com", href)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            rank += 1
            items.append(_build_item(rank, title, url))

    logger.info(f"Collected {len(items)} items from Livedoor News ranking")
    return items, None


def _build_item(rank: int, title: str, url: str) -> CollectedItem:
    return {
        "external_id": f"livedoor_news_{rank}",
        "title": title,
        "url": url,
        "locale": "ja-JP",
        "published_at": datetime.now(timezone.utc).isoformat(),
        "rank_position": rank,
        "engagement_signals": {
            "rank_position": rank,
        },
        "raw_payload": {
            "rank": rank,
            "title": title,
            "url": url,
            "source": "livedoor_news_ranking",
        },
    }
