"""
Papers With Code Trending surface collector.

Fetches trending ML papers from paperswithcode.com/latest.
Lists new ML papers with GitHub stars and trending scores. No auth required.

NOTE: Site layout changes occasionally. Lower priority surface — run a live
selector test if selectors stop returning results.

URL: https://paperswithcode.com/latest

Surface type: ranking
Bucket: category_tech
Locale: en-US
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple, List
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

PAPERSWITHCODE_URL = "https://paperswithcode.com/latest"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://paperswithcode.com/",
}


def _parse_stars(text: str) -> int:
    """Parse GitHub star count from badge text like '1.2k' or '340'."""
    text = text.strip().lower().replace(",", "")
    if not text:
        return 0
    try:
        if text.endswith("k"):
            return int(float(text[:-1]) * 1000)
        return int(text)
    except ValueError:
        return 0


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect trending ML papers from Papers With Code.

    Scrapes the public /latest page. No authentication required.
    Primary selectors (in order of preference):
      1. div.paper-card
      2. div.row > div.col-lg-9

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
    logger.info("Fetching Papers With Code trending papers...")

    async with RateLimitedClient(timeout=30.0) as client:
        response = await client.get(PAPERSWITHCODE_URL, headers=DEFAULT_HEADERS)
        response.raise_for_status()
        html = response.text

    soup = BeautifulSoup(html, "html.parser")
    items: List[CollectedItem] = []
    seen_urls: set = set()
    rank = 0

    # Primary selector 1: div.paper-card
    cards = soup.select("div.paper-card")

    # Primary selector 2: div.row > div.col-lg-9
    if not cards:
        cards = soup.select("div.row > div.col-lg-9")

    if cards:
        for card in cards:
            if rank >= limit:
                break

            # Title from h1 or h2 inside card
            title_tag = card.select_one("h1") or card.select_one("h2")
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            if not title or len(title) < 5:
                continue

            # URL from the first link in the card
            a_tag = card.select_one("a")
            if not a_tag:
                continue
            href = a_tag.get("href", "")
            if not href:
                continue
            url = urljoin("https://paperswithcode.com", href)
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # GitHub stars from badge
            star_badge = card.select_one("span.badge-secondary")
            github_stars = 0
            if star_badge:
                github_stars = _parse_stars(star_badge.get_text(strip=True))

            rank += 1

            items.append({
                "external_id": f"pwc_{rank}",
                "title": title,
                "url": url,
                "locale": "en-US",
                "published_at": datetime.now(timezone.utc).isoformat(),
                "rank_position": rank,
                "engagement_signals": {
                    "github_stars": github_stars,
                    "rank_position": rank,
                },
                "raw_payload": {
                    "rank": rank,
                    "title": title,
                    "url": url,
                    "github_stars": github_stars,
                    "source": "paperswithcode_trending",
                },
            })
    else:
        logger.warning("Papers With Code: no cards found — selectors may need updating")

    logger.info(f"Collected {len(items)} trending papers from Papers With Code")
    return items, None
