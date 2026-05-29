"""
Mirror Media (鏡週刊) collector — Playwright headless scraper.

Scrapes the Mirror Media news category page using headless Chromium.
Confirmed working 2026-04-11: domcontentloaded + 4s wait → 15+ article links.

Source page:
  https://www.mirrormedia.mg/category/news

Config (config_json):
  {
    "url":         "https://www.mirrormedia.mg/category/news",
    "locale":      "zh-Hant",
    "source_name": "mirrormedia_news"
  }

Key selector insight (validated 2026-04-11):
  - Article slugs follow pattern: /story/YYYYMMDD[type][id]  e.g. /story/20260410inv008
  - The date prefix (8 digits) distinguishes real articles from footer/nav links
    which also use /story/ but have non-date slugs (webauthorize, ad1018001, etc.)
  - Filter: slug must start with 8 digits (date)

Locale: zh-Hant  |  Region: tw  |  Bucket: news
"""

import logging
import re
from typing import List, Optional, Tuple

from .collector_interface import CollectedItem
from .base_playwright import PlaywrightPage, extract_links

logger = logging.getLogger(__name__)

DEFAULT_URL = "https://www.mirrormedia.mg/category/news"
BASE_URL = "https://www.mirrormedia.mg"

# Real articles: /story/YYYYMMDD[type][id] — slug starts with 8 digits
_STORY_SLUG_RE = re.compile(r"/story/(\d{8})")


def _is_story_href(href: str) -> bool:
    """
    Return True if href is a Mirror Media news article link.

    Accepts both absolute (https://...) and relative (/story/...) forms.
    Rejects footer/nav links like /story/webauthorize, /story/ad1018001
    which also start with /story/ but have non-date slugs.
    """
    return bool(_STORY_SLUG_RE.search(href))


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int,
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect latest articles from Mirror Media using Playwright.

    Args:
        config:  Surface config_json dict.
        cursor:  Unused — page always shows current articles.
        limit:   Maximum items to return.

    Returns:
        (items, next_cursor) — next_cursor always None.
    """
    url = config.get("url", DEFAULT_URL)
    locale = config.get("locale", "zh-Hant")
    source_name = config.get("source_name", "mirrormedia_news")

    logger.info(f"Mirror Media: collecting from {url} (limit={limit})")

    try:
        async with PlaywrightPage(url, wait_ms=4000) as page:
            links = await extract_links(
                page,
                href_filter=_is_story_href,
                base_url=BASE_URL,
                limit=limit,
            )
    except Exception as e:
        logger.error(f"Mirror Media: Playwright error: {e}", exc_info=True)
        return [], None

    if not links:
        logger.warning("Mirror Media: no story links found — page structure may have changed")
        return [], None

    # Filter out footer/nav items that happen to use /story/ paths:
    # - Short titles (≤12 chars): "下載APP", "新聞自律綱要", "廣告合作"
    # - Old permanent pages (year < 2024): e.g. /story/20161228corpmkt001/
    def _is_real_article(link: dict) -> bool:
        if len(link["title"]) <= 12:
            return False
        m = _STORY_SLUG_RE.search(link["href"])
        if m:
            year = int(m.group(1)[:4])
            if year < 2024:
                return False
        return True

    links = [l for l in links if _is_real_article(l)]
    logger.info(f"Mirror Media: found {len(links)} story links (after footer filter)")

    items: List[CollectedItem] = []
    for rank, link in enumerate(links[:limit], start=1):
        href = link["href"]
        title = link["title"]

        # story slug: last path segment e.g. "20260410inv008"
        slug = href.rstrip("/").split("/story/")[-1].rstrip("/")
        external_id = f"{source_name}_{slug}"

        item: CollectedItem = {
            "title": title,
            "url": href,
            "locale": locale,
            "rank_position": rank,
            "external_id": external_id,
            "engagement_signals": {},
            "raw_payload": {
                "source": source_name,
                "href": href,
                "title": title,
                "slug": slug,
            },
        }
        items.append(item)

    logger.info(f"Mirror Media: collected {len(items)} items")
    return items, None
