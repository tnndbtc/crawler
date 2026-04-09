"""
Papers With Code / Hugging Face Trending Papers surface collector.

Fetches trending ML papers from Hugging Face's papers page.
(paperswithcode.com/latest now redirects to huggingface.co/papers/trending)

No auth required. Scrapes public page for paper titles and links.

URL: https://huggingface.co/papers/trending

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

# paperswithcode.com/latest redirects here
PAPERS_URL = "https://huggingface.co/papers/trending"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://huggingface.co/",
}


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect trending ML papers from Hugging Face.

    Scrapes the public /papers/trending page. No authentication required.
    Finds paper links matching /papers/{arxiv_id} pattern and extracts titles.

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
    logger.info("Fetching trending papers from Hugging Face...")

    async with RateLimitedClient(timeout=30.0) as client:
        response = await client.get(PAPERS_URL, headers=DEFAULT_HEADERS)
        response.raise_for_status()
        html = response.text

    soup = BeautifulSoup(html, "html.parser")
    items: List[CollectedItem] = []
    seen_urls: set = set()
    rank = 0

    # Find all paper links: <a href="/papers/XXXX.XXXXX">paper title</a>
    for a_tag in soup.select('a[href*="/papers/"]'):
        if rank >= limit:
            break

        href = a_tag.get("href", "")
        # Only match actual paper IDs (e.g., /papers/2508.19205), not /papers/trending
        if not href or "/papers/trending" in href or "/papers/new" in href:
            continue
        # Must look like an arxiv ID path
        parts = href.rstrip("/").split("/")
        if len(parts) < 3 or not any(c.isdigit() for c in parts[-1]):
            continue

        title = a_tag.get_text(strip=True)
        if not title or len(title) < 10:
            continue

        url = urljoin("https://huggingface.co", href)
        if url in seen_urls:
            continue
        seen_urls.add(url)

        rank += 1

        items.append({
            "external_id": f"pwc_{parts[-1]}",
            "title": title,
            "url": url,
            "locale": "en-US",
            "published_at": datetime.now(timezone.utc).isoformat(),
            "rank_position": rank,
            "engagement_signals": {
                "rank_position": rank,
            },
            "raw_payload": {
                "rank": rank,
                "title": title,
                "url": url,
                "arxiv_id": parts[-1],
                "source": "paperswithcode_trending",
            },
        })

    logger.info(f"Collected {len(items)} trending papers from Hugging Face")
    return items, None
