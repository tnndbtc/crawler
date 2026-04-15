"""
YouTube surface collector.

Uses YouTube Data API v3. Requires YOUTUBE_API_KEY environment variable.

Modes (set via config_json.mode):
  "trending"  — mostPopular chart per region (default, existing behaviour)
  "search"    — query-based search (config: query, order)
  "channel"   — latest uploads from a specific channel (config: channel_id)

ENHANCED features (migrated from source project):
- Dual-mode fetching: Trending videos + Popular recent videos
- Trending: Uses mostPopular chart for current trending content
- Popular: Uses search for most viewed videos from last 24 hours
- Configurable via dual_mode config parameter

Surface type: ranking
Bucket: category_entertainment

Migrated from: /home/tnnd/data/code/trend/trend_agent/ingestion/plugins/youtube.py
"""

import html
import os
import logging
from typing import Optional, Tuple, List
from datetime import datetime, timedelta, timezone

import httpx

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient
from shared.comment_settings import get_top_comments_count

logger = logging.getLogger(__name__)

# YouTube Data API v3 endpoint
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

# Maximum number of videos to attempt comment fetching on per run.
COMMENT_FETCH_POST_LIMIT = 20


async def fetch_top_comments(
    client: httpx.AsyncClient,
    api_key: str,
    video_id: str,
    limit: int,
) -> List[str]:
    """
    Fetch top comments for a YouTube video.

    Uses commentThreads.list with order=relevance so the most
    meaningful viewer reactions appear first.

    Args:
        client: HTTP client
        api_key: YouTube Data API key
        video_id: YouTube video ID
        limit: Max number of comments to fetch (resolved from config or SystemSettings)

    Returns:
        List of comment text strings, empty on failure
    """
    params = {
        'part': 'snippet',
        'videoId': video_id,
        'order': 'relevance',
        'maxResults': limit,
        'key': api_key,
        'textFormat': 'plainText',
    }

    try:
        response = await client.get(
            f"{YOUTUBE_API_BASE}/commentThreads",
            params=params
        )
        response.raise_for_status()
        data = response.json()

        comments = []
        for item in data.get('items', []):
            text = (
                item.get('snippet', {})
                    .get('topLevelComment', {})
                    .get('snippet', {})
                    .get('textOriginal', '')
                    .strip()
            )
            if text:
                comments.append(text[:300])

        return comments

    except Exception as e:
        logger.debug(f"Could not fetch comments for YouTube video {video_id}: {e}")
        return []


async def fetch_popular_recent_videos(
    client: httpx.AsyncClient,
    api_key: str,
    region_code: str,
    limit: int,
    locale: str,
    top_comments_count: int,
    start_rank: int = 1,
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
        top_comments_count: Number of comments to fetch per video (0 = disabled)
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

            # Fetch top comments for top-ranked videos (skipped when count == 0)
            top_comments: List[str] = []
            if top_comments_count > 0 and rank <= COMMENT_FETCH_POST_LIMIT:
                top_comments = await fetch_top_comments(
                    client, api_key, video_id, limit=top_comments_count
                )

            # Extract best available thumbnail URL
            thumbnails = snippet.get('thumbnails', {})
            thumbnail_url = (
                thumbnails.get('maxres', {}).get('url') or
                thumbnails.get('high', {}).get('url') or
                thumbnails.get('medium', {}).get('url') or
                thumbnails.get('default', {}).get('url') or
                f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
            )

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
                    "_source": "popular_recent",
                    "top_comments": top_comments,
                    "_thumbnail_url": thumbnail_url,
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


async def _get_uploads_playlist_id(
    client: httpx.AsyncClient,
    api_key: str,
    channel_id: str,
) -> Optional[str]:
    """
    Step 1 of channel mode: resolve channel_id → uploads playlist ID.

    The uploads playlist ID is the same as the channel_id with 'UC' replaced
    by 'UU', but fetching it via API is more robust for edge cases.

    Args:
        client: HTTP client
        api_key: YouTube Data API key
        channel_id: YouTube channel ID (e.g. "UCNye-wNBqNL5ZzHSJj3l8Bg")

    Returns:
        Uploads playlist ID string, or None if channel not found
    """
    params = {
        "part": "contentDetails",
        "id": channel_id,
        "key": api_key,
    }
    try:
        response = await client.get(f"{YOUTUBE_API_BASE}/channels", params=params)
        response.raise_for_status()
        data = response.json()
        items = data.get("items", [])
        if not items:
            logger.warning(f"YouTube channel not found: {channel_id}")
            return None
        return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    except Exception as e:
        logger.error(f"Failed to resolve uploads playlist for channel {channel_id}: {e}")
        return None


async def _collect_search_mode(
    client: httpx.AsyncClient,
    api_key: str,
    query: str,
    locale: str,
    limit: int,
    order: str = "relevance",
) -> List[CollectedItem]:
    """
    Search mode: fetch videos matching a text query.

    Maps to config_json: {"mode": "search", "query": "breaking news"}

    Args:
        client: HTTP client
        api_key: YouTube Data API key
        query: Search query string
        locale: Locale string for output items
        limit: Max videos to return (capped at 50 by API)
        order: Sort order — "relevance" (default) or "date" or "viewCount"

    Returns:
        List of CollectedItem dicts
    """
    params = {
        "part": "snippet",
        "type": "video",
        "q": query,
        "order": order,
        "maxResults": min(limit, 50),
        "key": api_key,
        "safeSearch": "moderate",
    }

    logger.info(f"YouTube search mode: query={query!r}, order={order}, limit={limit}")

    try:
        response = await client.get(f"{YOUTUBE_API_BASE}/search", params=params)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        logger.error(f"YouTube search API error for query={query!r}: {e}")
        return []

    items: List[CollectedItem] = []
    for rank, entry in enumerate(data.get("items", []), start=1):
        video_id = entry.get("id", {}).get("videoId")
        if not video_id:
            continue
        snippet = entry.get("snippet", {})
        title = html.unescape(snippet.get("title", "Untitled"))
        description = html.unescape(snippet.get("description", ""))
        published_at = snippet.get("publishedAt")
        channel_title = snippet.get("channelTitle", "")

        thumbnails = snippet.get("thumbnails", {})
        thumbnail_url = (
            thumbnails.get("high", {}).get("url") or
            thumbnails.get("medium", {}).get("url") or
            thumbnails.get("default", {}).get("url") or
            f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
        )

        item: CollectedItem = {
            "external_id": video_id,
            "title": title,
            "description": description[:1000] if description else None,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "locale": locale,
            "published_at": published_at,
            "rank_position": rank,
            "engagement_signals": {},
            "raw_payload": {
                **entry,
                "_source": "search",
                "_query": query,
                "_thumbnail_url": thumbnail_url,
                "channelTitle": channel_title,
            },
        }
        items.append(item)

    logger.info(f"YouTube search collected {len(items)} items for query={query!r}")
    return items


async def _collect_channel_mode(
    client: httpx.AsyncClient,
    api_key: str,
    channel_id: str,
    locale: str,
    limit: int,
) -> List[CollectedItem]:
    """
    Channel mode: fetch latest uploads from a specific YouTube channel.

    Uses a 2-step API call:
      1. channels?part=contentDetails → uploads playlist ID
      2. playlistItems?playlistId=... → latest video list

    Maps to config_json: {"mode": "channel", "channel_id": "UCNye-wNBqNL5ZzHSJj3l8Bg"}

    Args:
        client: HTTP client
        api_key: YouTube Data API key
        channel_id: YouTube channel ID
        locale: Locale string for output items
        limit: Max videos to return (capped at 50 by API)

    Returns:
        List of CollectedItem dicts
    """
    logger.info(f"YouTube channel mode: channel_id={channel_id}, limit={limit}")

    # Step 1: resolve channel → uploads playlist
    playlist_id = await _get_uploads_playlist_id(client, api_key, channel_id)
    if not playlist_id:
        return []

    logger.info(f"Resolved uploads playlist: {playlist_id}")

    # Step 2: fetch latest videos from uploads playlist
    params = {
        "part": "snippet",
        "playlistId": playlist_id,
        "maxResults": min(limit, 50),
        "key": api_key,
    }

    try:
        response = await client.get(f"{YOUTUBE_API_BASE}/playlistItems", params=params)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        logger.error(f"YouTube playlistItems error for playlist {playlist_id}: {e}")
        return []

    items: List[CollectedItem] = []
    for rank, entry in enumerate(data.get("items", []), start=1):
        snippet = entry.get("snippet", {})
        video_id = snippet.get("resourceId", {}).get("videoId")
        if not video_id:
            continue

        title = html.unescape(snippet.get("title", "Untitled"))
        description = html.unescape(snippet.get("description", ""))
        published_at = snippet.get("publishedAt")
        channel_title = snippet.get("channelTitle", "")

        thumbnails = snippet.get("thumbnails", {})
        thumbnail_url = (
            thumbnails.get("maxres", {}).get("url") or
            thumbnails.get("high", {}).get("url") or
            thumbnails.get("medium", {}).get("url") or
            thumbnails.get("default", {}).get("url") or
            f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
        )

        item: CollectedItem = {
            "external_id": video_id,
            "title": title,
            "description": description[:1000] if description else None,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "locale": locale,
            "published_at": published_at,
            "rank_position": rank,
            "engagement_signals": {},
            "raw_payload": {
                **entry,
                "_source": "channel",
                "_channel_id": channel_id,
                "_playlist_id": playlist_id,
                "_thumbnail_url": thumbnail_url,
                "channelTitle": channel_title,
            },
        }
        items.append(item)

    logger.info(f"YouTube channel collected {len(items)} items from {channel_id}")
    return items


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect YouTube videos using Data API v3.

    Dispatches to one of three modes based on config_json.mode:

      mode="trending" (default)
        Fetches the mostPopular chart for a region.
        Config keys: region_code, video_category_id, locale, dual_mode,
                     top_comments (override comments per video; 0 = disable)

      mode="search"
        Fetches videos matching a text query.
        Config keys: query, order (default: "relevance"), locale

      mode="channel"
        Fetches latest uploads from a specific channel (2-step API).
        Config keys: channel_id, locale

    Args:
        config: Surface configuration (TrendSurface.config_json)
        cursor: Pagination cursor — used only in trending mode (pageToken)
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

    locale = config.get('locale', 'en-US')
    mode = config.get('mode', 'trending')

    # Resolve how many comments to fetch per video (0 = disabled)
    top_comments_count = await get_top_comments_count(config)

    # ── Search mode ───────────────────────────────────────────────────────────
    if mode == 'search':
        query = config.get('query', '')
        if not query:
            raise ValueError("YouTube search mode requires config_json.query to be set")
        order = config.get('order', 'relevance')
        async with RateLimitedClient(timeout=30.0) as client:
            items = await _collect_search_mode(
                client=client,
                api_key=api_key,
                query=query,
                locale=locale,
                limit=limit,
                order=order,
            )
        return items, None  # search has no pagination cursor

    # ── Channel mode ──────────────────────────────────────────────────────────
    if mode == 'channel':
        channel_id = config.get('channel_id', '')
        if not channel_id:
            raise ValueError("YouTube channel mode requires config_json.channel_id to be set")
        async with RateLimitedClient(timeout=30.0) as client:
            items = await _collect_channel_mode(
                client=client,
                api_key=api_key,
                channel_id=channel_id,
                locale=locale,
                limit=limit,
            )
        return items, None  # channel mode has no pagination cursor

    # ── Trending mode (default) ───────────────────────────────────────────────
    # Parse config
    region_code = config.get('region_code', 'US')
    video_category_id = config.get('video_category_id')  # Optional
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
            title = html.unescape(snippet.get('title', 'Untitled'))
            description = html.unescape(snippet.get('description', ''))
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

            # Fetch top comments for top-ranked videos (skipped when count == 0)
            top_comments: List[str] = []
            if top_comments_count > 0 and rank <= COMMENT_FETCH_POST_LIMIT:
                top_comments = await fetch_top_comments(
                    client, api_key, video_id, limit=top_comments_count
                )

            # Extract best available thumbnail URL
            thumbnails = snippet.get('thumbnails', {})
            thumbnail_url = (
                thumbnails.get('maxres', {}).get('url') or
                thumbnails.get('high', {}).get('url') or
                thumbnails.get('medium', {}).get('url') or
                thumbnails.get('default', {}).get('url') or
                f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
            )

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
                # ENHANCED: Mark source as trending mode + top comments + thumbnail
                "raw_payload": {
                    **video,
                    "_source": "trending",
                    "top_comments": top_comments,
                    "_thumbnail_url": thumbnail_url,
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
                top_comments_count=top_comments_count,
                start_rank=len(items) + 1,  # Continue ranking after trending videos
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
