"""
Naver News Ranking surface collector.

Fetches daily popular news ranking from Naver (naver.com), Korea's dominant
portal (~70% search share). The ranking page is fully public, no login required.

URL: https://news.naver.com/main/ranking/popularDay.naver

Surface type: ranking
Bucket: hot_now
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

NAVER_RANKING_URL = "https://news.naver.com/main/ranking/popularDay.naver"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://news.naver.com/",
}


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect daily popular news rankings from Naver News.

    Scrapes the public ranking page at news.naver.com. No authentication required.
    Primary selector: ul.rankingnews_list > li
    Fallback selector: a[href*="/article/"] anchors de-duped by href.

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
    logger.info("Fetching Naver News daily ranking...")

    async with RateLimitedClient(timeout=30.0) as client:
        response = await client.get(NAVER_RANKING_URL, headers=DEFAULT_HEADERS)
        response.raise_for_status()
        html = response.text

    soup = BeautifulSoup(html, "html.parser")
    items: List[CollectedItem] = []
    seen_urls: set = set()
    rank = 0

    # Primary selector: ul.rankingnews_list > li
    ranking_lists = soup.select("ul.rankingnews_list")
    if ranking_lists:
        for ul in ranking_lists:
            for li in ul.select("li"):
                if rank >= limit:
                    break
                a_tag = li.select_one(".list_title") or li.select_one("a")
                if not a_tag:
                    continue
                title = a_tag.get_text(strip=True)
                href = a_tag.get("href", "")
                if not href or not title:
                    continue
                url = urljoin("https://news.naver.com", href)
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                rank += 1
                items.append(_build_item(rank, title, url))
            if rank >= limit:
                break

    # Fallback: any article links if primary selector found nothing
    if not items:
        logger.warning("Naver primary selector found nothing — using fallback")
        for a_tag in soup.select('a[href*="/article/"]'):
            if rank >= limit:
                break
            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")
            if not href or not title or len(title) < 5:
                continue
            url = urljoin("https://news.naver.com", href)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            rank += 1
            items.append(_build_item(rank, title, url))

    logger.info(f"Collected {len(items)} items from Naver News ranking")
    return items, None


def _build_item(rank: int, title: str, url: str) -> CollectedItem:
    return {
        "external_id": f"naver_news_{rank}",
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
            "source": "naver_news_ranking",
        },
    }
