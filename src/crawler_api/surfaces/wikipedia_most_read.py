"""
Wikipedia Most Read surface collector.

Fetches the top most-read Wikipedia articles per language per day using the
Wikimedia Pageviews API. No key, no auth, no scraping required.
Available in 50+ languages — one collector, configured per region via config_json.

API endpoint pattern:
  https://wikimedia.org/api/rest_v1/metrics/pageviews/top/{lang}.wikipedia/all-access/{YYYY}/{MM}/{DD}

Response: JSON with items[0].articles[].article, items[0].articles[].views

Surface type: ranking
Bucket: hot_now
Poll interval: 86400 (daily, since the API is day-granular)
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, List

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

PAGEVIEWS_API_BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews/top"

# Pages to filter out — disambiguation, navigation, meta pages
BLOCKLIST = {
    "Main_Page", "Special:Search", "Wikipedia", "404_error",
    "-", ".", ".xxx",
}
BLOCKLIST_PREFIXES = ("Portal:", "Template:", "Help:", "Wikipedia:", "Special:")
BLOCKLIST_SUFFIXES = (" (disambiguation)",)


def _is_filtered(title: str) -> bool:
    """Return True if this article should be excluded."""
    if title in BLOCKLIST:
        return True
    for prefix in BLOCKLIST_PREFIXES:
        if title.startswith(prefix):
            return True
    for suffix in BLOCKLIST_SUFFIXES:
        if title.endswith(suffix):
            return True
    return False


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect top most-read Wikipedia articles for a given language.

    Uses yesterday's date (UTC) since today's data is incomplete until midnight.
    Filters out disambiguation pages, navigation pages, and meta pages using
    title matching — does NOT make per-article API calls (too slow at 50 items/run).

    Config params (from TrendSurface.config_json):
        lang (str): Wikipedia language code, e.g. "en", "ja", "ko", "zh".
                    REQUIRED — must be explicitly set per region.
        locale (str): BCP-47 locale tag for the collected items, e.g. "en-US".
                      REQUIRED — do not derive from lang (e.g. "pt" → pt-BR or pt-PT).

    Args:
        config: Surface configuration with lang and locale
        cursor: Pagination cursor (unused — daily API, no pagination)
        limit: Maximum items to collect

    Returns:
        (items, None) — no pagination cursor

    Raises:
        ValueError: If lang or locale is missing from config
        httpx.HTTPError: On API request failures
    """
    lang = config.get("lang")
    locale = config.get("locale", "en-US")

    if not lang:
        raise ValueError("wikipedia_most_read requires 'lang' in config_json")

    # Use yesterday's date (UTC) — today's data is still accumulating
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    date_path = yesterday.strftime("%Y/%m/%d")

    # Pageviews API: /top/{project}/all-access/{YYYY}/{MM}/{DD}
    url = f"{PAGEVIEWS_API_BASE}/{lang}.wikipedia/all-access/{date_path}"

    logger.info(f"Fetching Wikipedia most-read articles (lang={lang}, date={date_path})...")

    async with RateLimitedClient(timeout=30.0) as client:
        response = await client.get(url, headers={
            "Accept": "application/json",
            "User-Agent": "CrawlerBot/1.0 (trend data collection)",
        })
        response.raise_for_status()
        data = response.json()

    # Pageviews API returns: { "items": [ { "articles": [...] } ] }
    raw_items = data.get("items", [])
    if not raw_items:
        logger.warning(f"No items returned from Pageviews API (lang={lang})")
        return [], None

    articles = raw_items[0].get("articles", [])
    items: List[CollectedItem] = []
    rank = 0

    for article in articles:
        if rank >= limit:
            break

        # Pageviews API uses "article" field (not "title")
        title = article.get("article", "")
        if not title or _is_filtered(title):
            continue

        views = article.get("views", 0)

        # Construct canonical URL (Pageviews API doesn't provide content_urls)
        desktop_url = f"https://{lang}.wikipedia.org/wiki/{title}"

        # Convert underscores to spaces for display title
        display_title = title.replace("_", " ")

        rank += 1

        items.append({
            "external_id": f"wiki_{lang}_{title}",
            "title": display_title,
            "url": desktop_url,
            "locale": locale,
            "published_at": yesterday.isoformat(),
            "rank_position": rank,
            "engagement_signals": {
                "views": views,
            },
            "raw_payload": {
                "title": title,
                "views": views,
                "lang": lang,
                "date": date_path,
                "source": "wikipedia_most_read",
            },
        })

    logger.info(
        f"Collected {len(items)} Wikipedia most-read articles "
        f"(lang={lang}, date={date_path})"
    )
    return items, None
