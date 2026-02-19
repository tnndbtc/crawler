"""
YouTube Trending surface collector.

Uses YouTube Data API v3 to fetch trending videos.
Requires YOUTUBE_API_KEY environment variable.

ENHANCED features (migrated from source project):
- Dual-mode fetching: Trending videos + Popular recent videos
- Trending: Uses mostPopular chart for current trending content
- Popular: Uses search for most viewed videos from last 24 hours
- Configurable via dual_mode config parameter

Surface type: ranking
Bucket: category_entertainment

Migrated from: /home/tnnd/data/code/trend/trend_agent/ingestion/plugins/youtube.py
"""

import os
import logging
from typing import Optional, Tuple, List
from datetime import datetime, timedelta, timezone

import httpx

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient

logger = logging.getLogger(__name__)

# YouTube Data API v3 endpoint
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


async def fetch_popular_recent_videos(
    client: httpx.AsyncClient,
    api_key: str,
    region_code: str,
    limit: int,
    locale: str,
    start_rank: int = 1
) -> List[CollectedItem]:
    """
    Fetch most viewed videos from the last 24 hours using search API.

    This is the second mode in dual-mode fetching, providing recently
    popular content that complements the trending chart.

    Args:
        client: HTTP client
        api_key: YouTube API key
        region_code: Region code
        limit: Maximum videos to fetch
        locale: Locale code
        start_rank: Starting rank position for these videos

    Returns:
        List of CollectedItem dicts
    """
    # Calculate time 24 hours ago
    published_after = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    # Build search API parameters
    # Note: Search API has lower max results (25-50 depending on quota)
    actual_limit = min(limit, 25)

    params = {
        'part': 'snippet',
        'type': 'video',
        'order': 'viewCount',
        'publishedAfter': published_after,
        'regionCode': region_code,
        'maxResults': actual_limit,
        'key': api_key,
        'safeSearch': 'moderate',  # Filter explicit content
    }

    logger.info(f"Fetching popular recent videos (last 24h): limit={actual_limit}")

    try:
        response = await client.get(
            f"{YOUTUBE_API_BASE}/search",
            params=params
        )
        response.raise_for_status()
        search_data = response.json()

        # Extract video IDs from search results
        video_ids = [
            item['id']['videoId']
            for item in search_data.get('items', [])
            if item.get('id', {}).get('kind') == 'youtube#video'
        ]

        if not video_ids:
            logger.info("No recent popular videos found")
            return []

        # Fetch detailed statistics for these videos
        # (search doesn't include statistics, need videos.list)
        videos_params = {
            'part': 'snippet,statistics,contentDetails',
            'id': ','.join(video_ids),
            'key': api_key,
        }

        response = await client.get(
            f"{YOUTUBE_API_BASE}/videos",
            params=videos_params
        )
        response.raise_for_status()
        videos_data = response.json()

        # Parse videos into CollectedItems
        items: List[CollectedItem] = []
        for rank, video in enumerate(videos_data.get('items', []), start=start_rank):
            video_id = video['id']
            snippet = video.get('snippet', {})
            statistics = video.get('statistics', {})

            title = snippet.get('title', 'Untitled')
            description = snippet.get('description', '')
            published_at = snippet.get('publishedAt')

            # Parse published_at
            if published_at:
                try:
                    datetime.fromisoformat(published_at.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    published_at = None

            # Extract engagement signals
            views = int(statistics.get('viewCount', 0))
            likes = int(statistics.get('likeCount', 0))
            comments = int(statistics.get('commentCount', 0))

            item: CollectedItem = {
                "external_id": video_id,
                "title": title,
                "description": description[:1000] if description else None,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "locale": locale,
                "published_at": published_at,
                "rank_position": rank,
                "engagement_signals": {
                    "views": views,
                    "upvotes": likes,
                    "comments": comments,
                },
                "raw_payload": {
                    **video,
                    "_source": "popular_recent"  # Mark as from popular mode
                },
            }
            items.append(item)

        logger.info(f"Fetched {len(items)} popular recent videos")
        return items

    except httpx.HTTPError as e:
        logger.error(f"HTTP error fetching popular recent videos: {e}")
        return []
    except Exception as e:
        logger.error(f"Error fetching popular recent videos: {e}")
        return []


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect trending videos from YouTube using Data API v3.

    ENHANCED: Supports dual-mode fetching to get both trending and popular recent content.

    Config params (from TrendSurface.config_json):
        - region_code: YouTube region code (default: "US")
        - video_category_id: Category ID to filter by (optional, default: all categories)
        - locale: Preferred locale for content (default: "en-US")
        - dual_mode: Enable dual-mode fetching (default: False)
            When True, fetches both:
            1. Trending videos (mostPopular chart)
            2. Popular recent videos (most viewed from last 24h)

    Args:
        config: Surface configuration
        cursor: Pagination cursor (pageToken from YouTube API)
        limit: Maximum items to collect

    Returns:
        (items, next_cursor)

    Raises:
        ValueError: If YOUTUBE_API_KEY is not set
        httpx.HTTPError: On API request failures

    Note:
        In dual-mode, trending videos are ranked first (1-N), then popular recent
        videos continue the ranking (N+1 onwards).
    """
    # Get API key from environment
    api_key = os.environ.get('YOUTUBE_API_KEY')
    if not api_key:
        raise ValueError(
            "YOUTUBE_API_KEY environment variable not set. "
            "Get your API key from: https://console.cloud.google.com/apis/credentials"
        )

    # Parse config
    region_code = config.get('region_code', 'US')
    video_category_id = config.get('video_category_id')  # Optional
    locale = config.get('locale', 'en-US')
    dual_mode = config.get('dual_mode', False)  # ENHANCED: Enable dual-mode fetching

    # YouTube API max is 50 per request
    # In dual-mode, split limit between trending and popular
    if dual_mode:
        trending_limit = min(limit // 2, 50)  # Half for trending
        popular_limit = limit - trending_limit  # Rest for popular
    else:
        trending_limit = min(limit, 50)
        popular_limit = 0

    # Build API request parameters for trending videos
    params = {
        'part': 'snippet,statistics,contentDetails',
        'chart': 'mostPopular',
        'regionCode': region_code,
        'maxResults': trending_limit,
        'key': api_key,
        'hl': locale,  # Preferred language for text
    }

    # Optional category filter
    if video_category_id:
        params['videoCategoryId'] = video_category_id

    # Pagination
    if cursor:
        params['pageToken'] = cursor

    mode_str = "dual-mode" if dual_mode else "single-mode"
    logger.info(
        f"Fetching YouTube videos ({mode_str}): region={region_code}, "
        f"category={video_category_id or 'all'}, trending={trending_limit}, popular={popular_limit}"
    )

    # Make API request(s) - keep client open for dual-mode
    # Use RateLimitedClient for resilience
    async with RateLimitedClient(timeout=30.0) as client:
        # Fetch trending videos
        response = await client.get(
            f"{YOUTUBE_API_BASE}/videos",
            params=params
        )
        response.raise_for_status()
        data = response.json()

        # Parse trending videos
        items: List[CollectedItem] = []
        video_items = data.get('items', [])

        logger.info(f"YouTube API returned {len(video_items)} trending videos")

        # Parse each trending video
        for rank, video in enumerate(video_items, start=1):
            video_id = video['id']
            snippet = video.get('snippet', {})
            statistics = video.get('statistics', {})
            content_details = video.get('content_details', {})

            # Extract core fields
            title = snippet.get('title', 'Untitled')
            description = snippet.get('description', '')
            published_at = snippet.get('publishedAt')
            channel_title = snippet.get('channelTitle', 'Unknown Channel')

            # Parse published_at to ensure it's valid ISO8601
            if published_at:
                try:
                    # YouTube API returns ISO8601, validate it
                    datetime.fromisoformat(published_at.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    published_at = None

            # Extract engagement signals
            # YouTube returns these as strings, convert to int
            views = int(statistics.get('viewCount', 0))
            likes = int(statistics.get('likeCount', 0))
            comments = int(statistics.get('commentCount', 0))

            # Build collected item
            item: CollectedItem = {
                "external_id": video_id,
                "title": title,
                "description": description[:1000] if description else None,  # Cap description
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "locale": locale,
                "published_at": published_at,

                # CRITICAL: rank_position (from /tmp/t9)
                "rank_position": rank,

                # CRITICAL: engagement_signals (from /tmp/t9)
                "engagement_signals": {
                    "views": views,
                    "upvotes": likes,  # Map likes to upvotes
                    "comments": comments,
                },

                # CRITICAL: raw_payload (from /tmp/t9)
                # ENHANCED: Mark source as trending mode
                "raw_payload": {
                    **video,
                    "_source": "trending"
                },
            }
            items.append(item)

        # ENHANCED: Fetch popular recent videos in dual-mode
        if dual_mode and popular_limit > 0:
            logger.info(f"Dual-mode enabled: fetching popular recent videos")
            popular_items = await fetch_popular_recent_videos(
                client=client,
                api_key=api_key,
                region_code=region_code,
                limit=popular_limit,
                locale=locale,
                start_rank=len(items) + 1  # Continue ranking after trending videos
            )
            items.extend(popular_items)

        # Get next page token for pagination (only for trending videos)
        # Note: In dual-mode, pagination only applies to trending part
        next_cursor = data.get('nextPageToken')

    # End of async with client block

    logger.info(
        f"Collected {len(items)} YouTube videos total. Next cursor: {next_cursor or 'None'}"
    )

    return items, next_cursor
