"""
Letterboxd Popular Films surface collector.

Fetches the "Popular this week" chart from letterboxd.com/films/popular/.
No login required. The page is public.

NOTE: Letterboxd uses dynamic CSS class names that may change periodically.
The data-film-slug / data-target-link approach is more stable than class selectors.

URL: https://letterboxd.com/films/popular/

Surface type: ranking
Bucket: category_entertainment
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

LETTERBOXD_URL = "https://letterboxd.com/films/popular/"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://letterboxd.com/",
}


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect popular films from Letterboxd's weekly chart.

    Scrapes the public "Popular this week" page. No authentication required.
    Primary selector: ul.poster-list > li div.film-poster
    Uses data-film-slug and img[alt] which are more stable than CSS class names.

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
    logger.info("Fetching Letterboxd popular films...")

    async with RateLimitedClient(timeout=30.0) as client:
        response = await client.get(LETTERBOXD_URL, headers=DEFAULT_HEADERS)
        response.raise_for_status()
        html = response.text

    soup = BeautifulSoup(html, "html.parser")
    items: List[CollectedItem] = []
    seen_slugs: set = set()
    rank = 0

    # Primary selector: div.film-poster (stable data attributes)
    film_posters = soup.select("div.film-poster")

    if film_posters:
        for poster in film_posters:
            if rank >= limit:
                break

            # film slug from data attribute
            slug = poster.get("data-film-slug", "")
            if not slug or slug in seen_slugs:
                continue

            # URL from data-target-link or constructed from slug
            href = poster.get("data-target-link", f"/film/{slug}/")
            url = urljoin("https://letterboxd.com", href)

            # Title from img[alt]
            img = poster.select_one("img")
            title = img.get("alt", "").strip() if img else ""
            if not title:
                # Fallback: use slug as readable title
                title = slug.replace("-", " ").title()

            seen_slugs.add(slug)
            rank += 1

            items.append({
                "external_id": f"letterboxd_{slug}",
                "title": title,
                "url": url,
                "locale": "en-US",
                "published_at": datetime.now(timezone.utc).isoformat(),
                "rank_position": rank,
                "engagement_signals": {
                    "rank_position": rank,
                },
                "raw_payload": {
                    "slug": slug,
                    "rank": rank,
                    "title": title,
                    "url": url,
                    "source": "letterboxd_popular",
                },
            })
    else:
        logger.warning("Letterboxd: primary selector (div.film-poster) found nothing")

    logger.info(f"Collected {len(items)} popular films from Letterboxd")
    return items, None
