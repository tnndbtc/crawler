"""
New York Times Top Stories collector — Official API.

Uses the NYT Top Stories API (free tier, 500 req/day).

API docs: https://developer.nytimes.com/docs/top-stories-product/1/overview

Config (config_json):
  {
    "sections":    ["world", "technology", "business", "science"],
    "locale":      "en",
    "source_name": "nytimes"
  }

API key: read from NYT_API_KEY environment variable.

Rate limits (free tier):
  - 500 requests/day, 5 requests/minute
  - 4 sections × 1 call = 4 req/hour → safe at poll_interval=3600

Locale: en  |  Region: us (93)  |  Bucket: news
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import httpx

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

NYT_BASE_URL = "https://api.nytimes.com/svc/topstories/v2/{section}.json"

DEFAULT_SECTIONS = ["world", "technology", "business", "science"]

# Delay between section calls to stay within 5 req/min limit
INTER_SECTION_DELAY = 13  # seconds — 60s / 5 req/min = 12s; 13 for safety


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int,
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect top stories from NYT by section.

    Reads NYT_API_KEY from the environment.
    Fetches each configured section, deduplicates by URL, returns up to limit items.

    Args:
        config:  Surface config_json dict.
        cursor:  Unused — API always returns current top stories.
        limit:   Maximum items to return.

    Returns:
        (items, next_cursor) — next_cursor is always None.
    """
    api_key = os.getenv("NYT_API_KEY", "").strip()
    if not api_key:
        logger.error("NYT News: NYT_API_KEY not set — skipping collection")
        return [], None

    sections = config.get("sections", DEFAULT_SECTIONS)
    locale = config.get("locale", "en")
    source_name = config.get("source_name", "nytimes")

    logger.info(f"NYT News: fetching {len(sections)} sections (limit={limit})")

    seen_urls: set = set()
    items: List[CollectedItem] = []
    rank = 1

    async with RateLimitedClient(timeout=20.0, follow_redirects=True) as client:
        for idx, section in enumerate(sections):
            if len(items) >= limit:
                break

            url = NYT_BASE_URL.format(section=section)
            try:
                response = await client.get(url, params={"api-key": api_key})
                response.raise_for_status()
                payload = response.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    logger.warning(f"NYT News: rate limited on section '{section}' — stopping")
                    break
                logger.error(f"NYT News: HTTP {e.response.status_code} on section '{section}'")
                continue
            except Exception as e:
                logger.error(f"NYT News: error fetching section '{section}': {e}")
                continue

            results = payload.get("results", [])
            logger.info(f"NYT News: section '{section}' → {len(results)} articles")

            for entry in results:
                if len(items) >= limit:
                    break

                article_url = (entry.get("url") or "").strip()
                if not article_url or article_url in seen_urls:
                    continue
                seen_urls.add(article_url)

                title = (entry.get("title") or "").strip()
                if not title:
                    continue

                abstract = (entry.get("abstract") or "").strip()
                published_raw = entry.get("published_date") or entry.get("updated_date") or ""
                published_at: Optional[str] = None
                if published_raw:
                    try:
                        # Format: "2026-04-11T10:42:06-04:00" or "2026-04-11T14:42:06Z"
                        dt = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
                        published_at = dt.astimezone(timezone.utc).isoformat()
                    except ValueError:
                        published_at = published_raw

                # Multimedia thumbnail (first available)
                media_url = ""
                multimedia = entry.get("multimedia") or []
                if multimedia and isinstance(multimedia, list):
                    media_url = multimedia[0].get("url", "") if multimedia[0] else ""

                external_id = f"nyt_{section}_{entry.get('short_url', article_url)}"

                item: CollectedItem = {
                    "title": title,
                    "url": article_url,
                    "locale": locale,
                    "rank_position": rank,
                    "external_id": external_id,
                    "description": abstract,
                    "engagement_signals": {},
                    "raw_payload": {
                        "source": source_name,
                        "section": section,
                        "byline": entry.get("byline", ""),
                        "subsection": entry.get("subsection", ""),
                        "item_type": entry.get("item_type", ""),
                        "short_url": entry.get("short_url", ""),
                        "thumbnail": media_url,
                        "published_date": published_raw,
                    },
                }
                if published_at:
                    item["published_at"] = published_at

                items.append(item)
                rank += 1

            # Rate limit: wait between section calls (5 req/min limit)
            if idx < len(sections) - 1 and len(items) < limit:
                await asyncio.sleep(INTER_SECTION_DELAY)

    logger.info(f"NYT News: collected {len(items)} items across {len(sections)} sections")
    return items, None
