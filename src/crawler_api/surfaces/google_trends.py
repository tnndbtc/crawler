"""
Google Trends surface collector.

Collects trending search queries from Google Trends via the public RSS feed.
No API key or library dependency required.

RSS endpoint:
  https://trends.google.com/trending/rss?geo={GEO}

Schedule: Every 3 hours
Max items: 20-30

Surface type: trending
Bucket: hot_now (real-time trending searches)
"""

import logging
from io import BytesIO
from typing import Optional, Tuple, List
from datetime import datetime, timezone
from urllib.parse import quote_plus

import feedparser

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

GOOGLE_TRENDS_RSS_BASE = "https://trends.google.com/trending/rss"


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect trending search queries from Google Trends RSS feed.

    Config params (from TrendSurface.config_json):
        - geo: Geographic location code (default: 'US')
        - locale: Locale code (default: 'en-US')

    Args:
        config: Surface configuration
        cursor: Not used (Google Trends provides current trends only)
        limit: Maximum items to collect

    Returns:
        (items, None) — no pagination cursor
    """
    geo = config.get('geo', 'US')
    locale = config.get('locale', 'en-US')

    rss_url = f"{GOOGLE_TRENDS_RSS_BASE}?geo={geo}"

    logger.info(f"Collecting Google Trends RSS: geo={geo}, url={rss_url}")

    async with RateLimitedClient(timeout=30.0) as client:
        response = await client.get(rss_url, headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/rss+xml, application/xml, text/xml",
        })
        response.raise_for_status()
        feed = feedparser.parse(BytesIO(response.content))

    if feed.get('bozo', False):
        logger.warning(f"Google Trends RSS feed parse warning: {feed.get('bozo_exception', '')}")

    entries = feed.get('entries', [])
    logger.info(f"Google Trends RSS returned {len(entries)} entries for geo={geo}")

    items: List[CollectedItem] = []

    for idx, entry in enumerate(entries[:limit]):
        title = entry.get('title', '').strip()
        if not title:
            continue

        # Use the entry link if available, otherwise construct a Trends URL
        link = entry.get('link', '')
        if not link:
            link = f"https://trends.google.com/trends/explore?q={quote_plus(title)}&geo={geo}"

        # Extract traffic estimate from ht:approx_traffic if available
        traffic = entry.get('ht_approx_traffic', '') or entry.get('ht_picture', '')

        # Extract description
        description = entry.get('summary', '') or entry.get('description', '')

        item: CollectedItem = {
            "external_id": f"google_trends_{geo}_{idx}_{datetime.now(timezone.utc).strftime('%Y%m%d')}",
            "title": title,
            "url": link,
            "locale": locale,
            "rank_position": idx + 1,
            "engagement_signals": {
                "score": float(limit - idx),
            },
            "raw_payload": {
                "query": title,
                "geo": geo,
                "rank": idx + 1,
                "traffic": traffic,
                "source_type": "daily_trends",
                "source_name": "Google Trends",
            },
        }

        if description:
            item["description"] = description[:2000]

        items.append(item)

    logger.info(f"Collected {len(items)} trends from Google Trends (geo={geo})")
    return items, None
