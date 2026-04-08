"""
Goo News Ranking surface collector.

Fetches the hourly news ranking from NTT's goo.ne.jp portal.
The ranking page at news.goo.ne.jp/ranking/ is fully public, no login required.
This page has been publicly stable for 10+ years.

URL: https://news.goo.ne.jp/ranking/

Surface type: ranking
Bucket: hot_now
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

GOO_RANKING_URL = "https://news.goo.ne.jp/ranking/"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://news.goo.ne.jp/",
}


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect hourly news ranking from goo News (news.goo.ne.jp).

    Scrapes the public ranking page. No authentication required.
    Primary selectors (in order of preference):
      1. div.ranking-list > ol > li
      2. div#ranking > ul > li

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
    logger.info("Fetching goo News ranking...")

    async with RateLimitedClient(timeout=30.0) as client:
        response = await client.get(GOO_RANKING_URL, headers=DEFAULT_HEADERS)
        response.raise_for_status()
        html = response.text

    soup = BeautifulSoup(html, "html.parser")
    items: List[CollectedItem] = []
    seen_urls: set = set()
    rank = 0

    # Primary selector 1: div.ranking-list > ol > li
    list_items = soup.select("div.ranking-list > ol > li")

    # Primary selector 2: div#ranking > ul > li
    if not list_items:
        list_items = soup.select("div#ranking > ul > li")

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
            # goo links may be relative paths like /article/...
            url = urljoin("https://news.goo.ne.jp", href)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            rank += 1
            items.append(_build_item(rank, title, url))

    # Fallback: any article-looking links
    if not items:
        logger.warning("goo News primary selectors found nothing — using fallback")
        for a_tag in soup.select('a[href*="/article/"]'):
            if rank >= limit:
                break
            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")
            if not href or not title or len(title) < 5:
                continue
            url = urljoin("https://news.goo.ne.jp", href)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            rank += 1
            items.append(_build_item(rank, title, url))

    logger.info(f"Collected {len(items)} items from goo News ranking")
    return items, None


def _build_item(rank: int, title: str, url: str) -> CollectedItem:
    return {
        "external_id": f"goo_news_{rank}",
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
            "source": "goo_news_ranking",
        },
    }
