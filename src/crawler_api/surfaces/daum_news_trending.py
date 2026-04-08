"""
Daum News Trending surface collector.

Fetches trending news from Daum (Kakao's portal), Korea's #2 news aggregator.
The news section at news.daum.net is fully public, no login required.

URL: https://news.daum.net/

Surface type: ranking
Bucket: rising
Locale: ko-KR
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple, List
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

DAUM_NEWS_URL = "https://news.daum.net/"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://news.daum.net/",
}


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect trending news from Daum News portal.

    Scrapes the public Daum News homepage. No authentication required.
    Primary selectors (in order of preference):
      1. ul.list_news2 > li
      2. div.box_ranking > ol > li
    Fallback: any <a> inside a container with "rank" in class name.

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
    logger.info("Fetching Daum News trending articles...")

    async with RateLimitedClient(timeout=30.0) as client:
        response = await client.get(DAUM_NEWS_URL, headers=DEFAULT_HEADERS)
        response.raise_for_status()
        html = response.text

    soup = BeautifulSoup(html, "html.parser")
    items: List[CollectedItem] = []
    seen_urls: set = set()
    rank = 0

    # Primary selector 1: ul.list_news2 > li
    list_items = soup.select("ul.list_news2 > li")

    # Primary selector 2: div.box_ranking > ol > li
    if not list_items:
        list_items = soup.select("div.box_ranking > ol > li")

    if list_items:
        for li in list_items:
            if rank >= limit:
                break
            a_tag = (
                li.select_one("strong.tit_thumb > a")
                or li.select_one("a.link_txt")
                or li.select_one("a")
            )
            if not a_tag:
                continue
            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")
            if not href or not title or len(title) < 5:
                continue
            url = urljoin("https://news.daum.net", href)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            rank += 1
            items.append(_build_item(rank, title, url))

    # Fallback: containers with "rank" in class
    if not items:
        logger.warning("Daum primary selectors found nothing — using fallback")
        for container in soup.find_all(class_=lambda c: c and "rank" in c):
            for a_tag in container.find_all("a"):
                if rank >= limit:
                    break
                title = a_tag.get_text(strip=True)
                href = a_tag.get("href", "")
                if not href or not title or len(title) < 5:
                    continue
                url = urljoin("https://news.daum.net", href)
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                rank += 1
                items.append(_build_item(rank, title, url))
            if rank >= limit:
                break

    logger.info(f"Collected {len(items)} items from Daum News")
    return items, None


def _build_item(rank: int, title: str, url: str) -> CollectedItem:
    return {
        "external_id": f"daum_news_{rank}",
        "title": title,
        "url": url,
        "locale": "ko-KR",
        "published_at": datetime.now(timezone.utc).isoformat(),
        "rank_position": rank,
        "engagement_signals": {
            "rank_position": rank,
        },
        "raw_payload": {
            "rank": rank,
            "title": title,
            "url": url,
            "source": "daum_news_trending",
        },
    }
