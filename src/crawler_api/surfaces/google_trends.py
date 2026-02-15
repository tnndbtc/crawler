"""
Google Trends surface collector.

Collects trending search queries and topics from Google Trends using
the pytrends library (unofficial Google Trends API).

Fetches both:
1. Daily trending searches (most searched topics of the day)
2. Realtime trends (currently trending topics)

Schedule: Every 3 hours
Max items: 20-30

Surface type: trending
Bucket: hot_now (real-time trending searches)

Migrated from: /home/tnnd/data/code/trend/trend_agent/ingestion/plugins/google_trends.py
"""

import asyncio
import logging
from typing import Optional, Tuple, List
from datetime import datetime, timezone

from .collector_interface import CollectedItem

logger = logging.getLogger(__name__)


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect trending search queries from Google Trends.

    This collector uses pytrends (unofficial Google Trends API) to fetch both
    daily trending searches and realtime trending searches.

    Config params (from TrendSurface.config_json):
        - geo: Geographic location code (default: 'US')
        - locale: Locale code (default: 'en-US')
        - max_daily: Max daily trending searches to fetch (default: 20)
        - max_realtime: Max realtime trends to fetch (default: 10)
        - include_realtime: Whether to include realtime trends (default: True)

    Args:
        config: Surface configuration
        cursor: Not used (Google Trends doesn't support pagination)
        limit: Maximum items to collect

    Returns:
        (items, next_cursor)

    Note:
        Cursor is always None since Google Trends provides current trends only,
        not historical pagination.

    Raises:
        ImportError: If pytrends is not installed
        Exception: On Google Trends API errors
    """
    # Parse config
    geo = config.get('geo', 'US')
    locale = config.get('locale', 'en-US')
    max_daily = config.get('max_daily', min(limit, 20))
    max_realtime = config.get('max_realtime', 10)
    include_realtime = config.get('include_realtime', True)

    logger.info(f"Collecting Google Trends: geo={geo}, daily={max_daily}, realtime={max_realtime}")

    try:
        # Import pytrends (lazy import in case it's not installed)
        from pytrends.request import TrendReq
    except ImportError:
        raise ImportError(
            "pytrends library not installed. Install with: pip install pytrends"
        )

    try:
        # Initialize pytrends
        pytrends = TrendReq(hl=locale, tz=360, timeout=(10, 25))

        items: List[CollectedItem] = []

        # Fetch daily trending searches
        daily_items = await fetch_daily_trends(
            pytrends=pytrends,
            geo=geo,
            locale=locale,
            max_trends=max_daily,
            start_rank=1
        )
        items.extend(daily_items)

        # Fetch realtime trends if enabled
        if include_realtime:
            realtime_items = await fetch_realtime_trends(
                pytrends=pytrends,
                geo=geo,
                locale=locale,
                max_trends=max_realtime,
                start_rank=len(items) + 1
            )
            items.extend(realtime_items)

        logger.info(f"Collected {len(items)} trends from Google Trends")

        # Google Trends doesn't support pagination
        return items, None

    except Exception as e:
        logger.error(f"Error collecting from Google Trends: {e}", exc_info=True)
        raise


async def fetch_daily_trends(
    pytrends,
    geo: str,
    locale: str,
    max_trends: int,
    start_rank: int = 1
) -> List[CollectedItem]:
    """
    Fetch daily trending searches from Google Trends.

    Args:
        pytrends: TrendReq instance
        geo: Geographic location code
        locale: Locale code
        max_trends: Maximum trends to fetch
        start_rank: Starting rank position

    Returns:
        List of CollectedItem dicts
    """
    try:
        # Run synchronous pytrends method in executor
        loop = asyncio.get_event_loop()
        df = await loop.run_in_executor(
            None,
            pytrends.trending_searches,
            geo
        )

        items: List[CollectedItem] = []

        # Process top N trends
        for idx, row in df.head(max_trends).iterrows():
            trend_query = str(row[0])

            # Create Google Trends URL
            url = f"https://trends.google.com/trends/explore?q={trend_query}&geo={geo}"

            # Build collected item
            item: CollectedItem = {
                "external_id": f"google_trends_{geo}_{idx}_{datetime.now(timezone.utc).strftime('%Y%m%d')}",
                "title": f"Trending: {trend_query}",
                "description": f"Trending search query on Google in {geo}",
                "url": url,
                "locale": locale,
                "rank_position": start_rank + idx,

                # Score based on ranking (higher rank = higher score)
                "engagement_signals": {
                    "score": float(max_trends - idx),
                },

                # Store trend data
                "raw_payload": {
                    "query": trend_query,
                    "geo": geo,
                    "rank": int(idx) + 1,
                    "source_type": "daily_trends",
                    "source_name": "Google Trends",
                },
            }
            items.append(item)

        logger.info(f"Fetched {len(items)} daily trending searches")
        return items

    except Exception as e:
        logger.error(f"Failed to fetch daily trends: {e}")
        return []


async def fetch_realtime_trends(
    pytrends,
    geo: str,
    locale: str,
    max_trends: int,
    start_rank: int = 1
) -> List[CollectedItem]:
    """
    Fetch realtime trending searches from Google Trends.

    Args:
        pytrends: TrendReq instance
        geo: Geographic location code
        locale: Locale code
        max_trends: Maximum trends to fetch
        start_rank: Starting rank position

    Returns:
        List of CollectedItem dicts
    """
    try:
        # Run synchronous pytrends method in executor
        loop = asyncio.get_event_loop()
        trending_data = await loop.run_in_executor(
            None,
            pytrends.realtime_trending_searches,
            geo
        )

        items: List[CollectedItem] = []

        # Process top N realtime trends
        for idx, trend in enumerate(trending_data.head(max_trends)):
            if isinstance(trend, dict):
                title = trend.get("title", "")
                traffic = trend.get("formattedTraffic", "")

                if not title:
                    continue

                # Create realtime trends URL
                url = f"https://trends.google.com/trends/trendingsearches/realtime?geo={geo}&category=all"

                # Build collected item
                item: CollectedItem = {
                    "external_id": f"google_trends_realtime_{geo}_{idx}_{datetime.now(timezone.utc).strftime('%Y%m%d%H')}",
                    "title": f"Realtime: {title}",
                    "description": f"Realtime trending search with {traffic} searches",
                    "url": url,
                    "locale": locale,
                    "rank_position": start_rank + idx,

                    # Score based on ranking
                    "engagement_signals": {
                        "score": float(max_trends - idx),
                        "traffic": traffic,
                    },

                    # Store trend data
                    "raw_payload": {
                        **trend,
                        "geo": geo,
                        "is_realtime": True,
                        "source_type": "realtime_trends",
                        "source_name": "Google Trends Realtime",
                    },
                }
                items.append(item)

        logger.info(f"Fetched {len(items)} realtime trends")
        return items

    except Exception as e:
        logger.warning(f"Failed to fetch realtime trends (may not be available for {geo}): {e}")
        return []
