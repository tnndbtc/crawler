"""
HK01 (香港01) collector — Playwright headless scraper.

Scrapes the HK01 channel/zone pages using a headless Chromium browser.
Confirmed working 2026-04-11: domcontentloaded + 4s wait → 54 links.

Source pages:
  - https://www.hk01.com/channel/2    (港聞 — HK local news, default)
  - https://www.hk01.com/zone/1       (alternative zone)

Config (config_json):
  {
    "urls":        ["https://www.hk01.com/channel/2"],
    "locale":      "zh-Hant",
    "source_name": "hk01_news"
  }

Key selector insight (validated 2026-04-11):
  - Correct pattern: /<url-encoded-category>/<8-digit-id>/<slug>
  - Filter:  re.search(r'/\\d{7,}/', href)
  - NOT /article/ — those are a different content type

Locale: zh-Hant  |  Region: cn (96, closest; tw not yet in DB)  |  Bucket: news
"""

import logging
import re
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from .collector_interface import CollectedItem
from .base_playwright import PlaywrightPage, extract_links

logger = logging.getLogger(__name__)

# /latest is the correct page — /channel/2 and /zone/1 don't surface article cards
# Confirmed: /latest returns 17+ article links with 7-digit numeric IDs (2026-04-11)
DEFAULT_URLS = ["https://www.hk01.com/latest"]
BASE_URL = "https://www.hk01.com"

# Confirmed selector (2026-04-11): numeric segment ≥7 digits in path
_ARTICLE_RE = re.compile(r"/\d{7,}/")


def _is_article_href(href: str) -> bool:
    """Return True if href matches the HK01 article URL pattern."""
    if not href.startswith(("https://www.hk01.com", "/")):
        return False
    path = urlparse(href).path if href.startswith("http") else href
    return bool(_ARTICLE_RE.search(path))


async def _collect_from_url(url: str, limit: int) -> List[dict]:
    """Scrape one HK01 page and return raw link dicts."""
    # 7s wait confirmed needed — articles render after nav elements (2026-04-11)
    async with PlaywrightPage(url, wait_ms=7000) as page:
        links = await extract_links(
            page,
            href_filter=_is_article_href,
            base_url=BASE_URL,
            limit=limit,
        )
    logger.info(f"HK01: {url} → {len(links)} article links")
    return links


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int,
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect latest articles from HK01 using Playwright.

    Args:
        config:  Surface config_json dict.
        cursor:  Unused — page always shows current articles.
        limit:   Maximum items to return.

    Returns:
        (items, next_cursor) — next_cursor always None.
    """
    urls = config.get("urls", DEFAULT_URLS)
    locale = config.get("locale", "zh-Hant")
    source_name = config.get("source_name", "hk01_news")

    logger.info(f"HK01: collecting from {len(urls)} page(s) (limit={limit})")

    seen_urls: set = set()
    all_links: List[dict] = []

    per_page = max(limit // len(urls), limit)
    for url in urls:
        try:
            links = await _collect_from_url(url, per_page)
            for link in links:
                if link["href"] not in seen_urls:
                    seen_urls.add(link["href"])
                    all_links.append(link)
                    if len(all_links) >= limit:
                        break
        except Exception as e:
            logger.error(f"HK01: error scraping {url}: {e}", exc_info=True)

        if len(all_links) >= limit:
            break

    if not all_links:
        logger.warning("HK01: no article links found across all pages")
        return [], None

    items: List[CollectedItem] = []
    for rank, link in enumerate(all_links[:limit], start=1):
        href = link["href"]
        title = link["title"]

        # Extract numeric article ID from path for external_id
        m = _ARTICLE_RE.search(href)
        article_id = m.group(0).strip("/") if m else href

        item: CollectedItem = {
            "title": title,
            "url": href,
            "locale": locale,
            "rank_position": rank,
            "external_id": f"{source_name}_{article_id}",
            "engagement_signals": {},
            "raw_payload": {
                "source": source_name,
                "href": href,
                "title": title,
            },
        }
        items.append(item)

    logger.info(f"HK01: collected {len(items)} items")
    return items, None
