"""
YouTube Trending surface collector.

Uses YouTube Data API v3 to fetch trending videos.
Requires YOUTUBE_API_KEY environment variable.

Surface type: ranking
Bucket: category_entertainment
"""

import os
import logging
from typing import Optional, Tuple, List
from datetime import datetime

import httpx

from .collector_interface import CollectedItem

logger = logging.getLogger(__name__)

# YouTube Data API v3 endpoint
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect trending videos from YouTube using Data API v3.

    Config params (from TrendSurface.config_json):
        - region_code: YouTube region code (default: "US")
        - video_category_id: Category ID to filter by (optional, default: all categories)
        - locale: Preferred locale for content (default: "en-US")

    Args:
        config: Surface configuration
        cursor: Pagination cursor (pageToken from YouTube API)
        limit: Maximum items to collect

    Returns:
        (items, next_cursor)

    Raises:
        ValueError: If YOUTUBE_API_KEY is not set
        httpx.HTTPError: On API request failures
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

    # YouTube API max is 50 per request
    actual_limit = min(limit, 50)

    # Build API request parameters
    params = {
        'part': 'snippet,statistics,contentDetails',
        'chart': 'mostPopular',
        'regionCode': region_code,
        'maxResults': actual_limit,
        'key': api_key,
        'hl': locale,  # Preferred language for text
    }

    # Optional category filter
    if video_category_id:
        params['videoCategoryId'] = video_category_id

    # Pagination
    if cursor:
        params['pageToken'] = cursor

    logger.info(
        f"Fetching YouTube trending videos: region={region_code}, "
        f"category={video_category_id or 'all'}, limit={actual_limit}"
    )

    # Make API request
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{YOUTUBE_API_BASE}/videos",
            params=params
        )
        response.raise_for_status()
        data = response.json()

    # Parse response
    items: List[CollectedItem] = []
    video_items = data.get('items', [])

    logger.info(f"YouTube API returned {len(video_items)} videos")

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
            # Store complete API response for this video
            "raw_payload": video,
        }
        items.append(item)

    # Get next page token for pagination
    next_cursor = data.get('nextPageToken')

    logger.info(
        f"Collected {len(items)} YouTube videos. Next cursor: {next_cursor or 'None'}"
    )

    return items, next_cursor
