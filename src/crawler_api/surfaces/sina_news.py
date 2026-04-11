"""
Sina News (新浪新闻) collector — JSON API.

Fetches the rolling news feed from Sina's WAP API endpoint.
Confirmed live as of 2026-04-11 (returns April 2026 articles).

Endpoint:
  https://interface.sina.cn/wap_api/news_roll.d.html

Config (config_json):
  {
    "api_url":     "https://interface.sina.cn/wap_api/news_roll.d.html",
    "locale":      "zh-Hans",
    "source_name": "sina_news"
  }

Notes:
- No session warmup or cookies required — plain GET works.
- Response is JSON with a list of article objects inside data.data.
- Items older than 24 h are skipped (feed occasionally bleeds in stale articles).
- Locale: zh-Hans  |  Region: cn  |  Bucket: news
"""

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

import httpx

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "https://interface.sina.cn/wap_api/news_roll.d.html"

# API query params confirmed working (2026-04-11)
# Result path: payload['result']['data']['list']
DEFAULT_PARAMS = {
    "pageid": "122",
    "lid": "2509",
    "num": "50",
    "versionNumber": "1",
}

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/20A362 "
        "MicroMessenger/8.0.38"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://news.sina.cn/",
}

# Drop articles older than this threshold (guards against stale bleed-in)
MAX_AGE_HOURS = 24

# Date in article URL: /2026-04-11/detail-xxx.html
_URL_DATE_RE = re.compile(r"/(\d{4})-(\d{2})-(\d{2})/")


def _date_from_url(url: str) -> Optional[datetime]:
    """Extract date from a Sina article URL like /2026-04-11/detail-xxx.html."""
    m = _URL_DATE_RE.search(url)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int,
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect latest news articles from Sina News WAP API.

    Args:
        config:  Surface config_json dict.
        cursor:  Unused — API returns current feed on every call.
        limit:   Maximum items to return.

    Returns:
        (items, next_cursor) — next_cursor is always None (no pagination needed).
    """
    api_url = config.get("api_url", DEFAULT_API_URL)
    locale = config.get("locale", "zh-Hans")
    source_name = config.get("source_name", "sina_news")

    logger.info(f"Sina News: fetching {api_url} (limit={limit})")

    params = {**DEFAULT_PARAMS, "num": str(max(limit, 50))}

    try:
        async with RateLimitedClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(api_url, params=params, headers=DEFAULT_HEADERS)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as e:
        logger.error(f"Sina News HTTP error: {e}")
        return [], None
    except Exception as e:
        logger.error(f"Sina News unexpected error: {e}", exc_info=True)
        return [], None

    # Navigate to the article list.
    # Confirmed structure (2026-04-11): payload['result']['data']['list']
    raw_list: list = []
    try:
        result = payload.get("result", {})
        if isinstance(result, dict):
            data = result.get("data", {})
            if isinstance(data, dict):
                raw_list = data.get("list", []) or []
    except AttributeError:
        pass

    if not raw_list:
        logger.warning(f"Sina News: empty article list — payload keys: {list(payload.keys())}")
        return [], None

    # cTime is a human-relative string ("9小时前"), so we use the date in the URL
    # as published_at instead. The URL always contains YYYY-MM-DD.
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=MAX_AGE_HOURS)

    items: List[CollectedItem] = []
    skipped_stale = 0

    for rank, entry in enumerate(raw_list, start=1):
        if len(items) >= limit:
            break

        try:
            title = (entry.get("title") or "").strip()
            # Field is uppercase "URL" in this API
            url = (entry.get("URL") or entry.get("bbs_url") or entry.get("reURL") or "").strip()
            if not title or not url:
                continue

            # Normalise URL — WAP entries sometimes use http
            if url.startswith("http://"):
                url = "https://" + url[7:]

            # Extract date from URL (cTime is a relative string, not parseable as timestamp)
            published_at = _date_from_url(url)

            # Filter stale items (if date can be determined)
            if published_at and published_at < cutoff:
                skipped_stale += 1
                continue

            external_id = str(entry.get("_id") or entry.get("id") or url)
            comment_count = 0
            try:
                comment_count = int(entry.get("comment") or 0)
            except (ValueError, TypeError):
                pass

            item: CollectedItem = {
                "title": title,
                "url": url,
                "locale": locale,
                "rank_position": rank,
                "external_id": external_id,
                "engagement_signals": {
                    "comments": comment_count,
                },
                "raw_payload": {
                    "source": source_name,
                    **{k: v for k, v in entry.items()},
                },
            }
            if published_at:
                item["published_at"] = published_at.isoformat()

            items.append(item)

        except Exception as e:
            logger.warning(f"Sina News: error parsing entry at rank {rank}: {e}")
            continue

    if skipped_stale:
        logger.info(f"Sina News: skipped {skipped_stale} stale items (>{MAX_AGE_HOURS}h)")

    logger.info(f"Sina News: collected {len(items)} items")
    return items, None
