"""
China Times (中時新聞網) collector — HTML scraper.

Scrapes the real-time news listing page.
Confirmed live as of 2026-04-11 (returns April 2026 articles).

Endpoint:
  https://www.chinatimes.com/realtimenews/?chdtv

Config (config_json):
  {
    "url":         "https://www.chinatimes.com/realtimenews/?chdtv",
    "locale":      "zh-Hant",
    "source_name": "chinatimes"
  }

Notes:
- RSS and sitemap return 403; the HTML listing page is open.
- Uses a mobile User-Agent + Referer to match typical browser behaviour.
- Article URLs follow pattern: /realtimenews/YYYYMMDDNNNNNN-NNNNNN
- Date is embedded in the article URL — used as published_at.
- Locale: zh-Hant  |  Region: cn (Taiwan; tw region not yet in DB)  |  Bucket: news
"""

import logging
import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

BASE_URL = "https://www.chinatimes.com"
DEFAULT_LISTING_URL = "https://www.chinatimes.com/realtimenews/?chdtv"

# Mobile UA avoids some bot-detection; Referer matches same origin
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/16.0 Mobile/15E148 Safari/604.1"
    ),
    "Referer": "https://www.chinatimes.com/",
    "Accept-Language": "zh-TW,zh;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Article URL date pattern: /realtimenews/20260411123456-NNNNNN
_URL_DATE_RE = re.compile(r"/realtimenews/(\d{4})(\d{2})(\d{2})\d+-\d+")


def _date_from_url(url: str) -> Optional[datetime]:
    """Extract publication date from a China Times article URL."""
    m = _URL_DATE_RE.search(url)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _parse_article_links(html: str, limit: int) -> List[dict]:
    """
    Parse article title + URL pairs from the realtimenews listing HTML.

    Confirmed selectors (live crawl 2026-04-11):
      - Article container: <li class="..." data-article-id="...">
      - Link + title: <h3 class="title"><a href="/realtimenews/...">...</a></h3>

    Falls back to any <a href="/realtimenews/..."> if the primary selector misses.
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []
    seen_urls: set = set()

    # Primary: h3.title > a inside li elements
    for anchor in soup.select("h3.title > a[href]"):
        href = anchor.get("href", "").strip()
        title = anchor.get_text(strip=True)
        if not href or not title:
            continue
        if not href.startswith("/realtimenews/"):
            continue
        full_url = urljoin(BASE_URL, href)
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)
        results.append({"title": title, "url": full_url, "href": href})
        if len(results) >= limit:
            return results

    # Fallback: any anchor pointing at a realtimenews article path
    if not results:
        logger.debug("China Times: primary selector found nothing — trying fallback")
        for anchor in soup.select("a[href^='/realtimenews/']"):
            href = anchor.get("href", "").strip()
            title = anchor.get_text(strip=True)
            if not href or not title or len(title) < 4:
                continue
            full_url = urljoin(BASE_URL, href)
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)
            results.append({"title": title, "url": full_url, "href": href})
            if len(results) >= limit:
                break

    return results


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int,
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect latest articles from China Times real-time news listing.

    Args:
        config:  Surface config_json dict.
        cursor:  Unused — listing page always shows current articles.
        limit:   Maximum items to return.

    Returns:
        (items, next_cursor) — next_cursor always None (no pagination).
    """
    listing_url = config.get("url", DEFAULT_LISTING_URL)
    locale = config.get("locale", "zh-Hant")
    source_name = config.get("source_name", "chinatimes")

    logger.info(f"China Times: fetching {listing_url} (limit={limit})")

    try:
        async with RateLimitedClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(listing_url, headers=DEFAULT_HEADERS)
            response.raise_for_status()
            html = response.text
    except httpx.HTTPError as e:
        logger.error(f"China Times HTTP error: {e}")
        return [], None
    except Exception as e:
        logger.error(f"China Times unexpected error: {e}", exc_info=True)
        return [], None

    parsed = _parse_article_links(html, limit)
    if not parsed:
        logger.warning("China Times: no article links found — page structure may have changed")
        return [], None

    items: List[CollectedItem] = []
    for rank, entry in enumerate(parsed, start=1):
        title = entry["title"]
        url = entry["url"]
        published_at = _date_from_url(entry["href"])

        # Build a stable external_id from the article path
        # e.g. /realtimenews/20260411123456-260409 → realtimenews_20260411123456-260409
        path_slug = entry["href"].lstrip("/").replace("/", "_")
        external_id = f"{source_name}_{path_slug}"

        item: CollectedItem = {
            "title": title,
            "url": url,
            "locale": locale,
            "rank_position": rank,
            "external_id": external_id,
            "engagement_signals": {},
            "raw_payload": {
                "source": source_name,
                "href": entry["href"],
                "title": title,
                "url": url,
            },
        }
        if published_at:
            item["published_at"] = published_at.isoformat()

        items.append(item)

    logger.info(f"China Times: collected {len(items)} items")
    return items, None
