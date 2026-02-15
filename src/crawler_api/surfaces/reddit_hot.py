"""
Reddit Hot surface collector.

Uses Reddit's public JSON API to fetch hot posts from subreddits.
No authentication required for public subreddits.

ENHANCED features (migrated from source project):
- NSFW content filtering (over_18 posts are skipped)
- Better URL determination (self posts use permalink, external links preserved)
- Richer metadata (subreddit, is_self, post_hint, domain, gilded awards)

Surface type: ranking
Bucket: hot_now (major trending content)
"""

import logging
from typing import Optional, Tuple, List
from datetime import datetime, timezone

import httpx

from .collector_interface import CollectedItem

logger = logging.getLogger(__name__)

# Reddit JSON API base URL
REDDIT_BASE = "https://www.reddit.com"


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect hot posts from Reddit using the public JSON API.

    Config params (from TrendSurface.config_json):
        - subreddit: Subreddit to collect from (default: "all")
        - time_filter: Time filter for hot posts (optional: hour, day, week, month, year, all)

    Args:
        config: Surface configuration
        cursor: Pagination cursor (Reddit's "after" parameter)
        limit: Maximum items to collect

    Returns:
        (items, next_cursor)

    Raises:
        httpx.HTTPError: On API request failures
    """
    # Parse config
    subreddit = config.get('subreddit', 'all')
    time_filter = config.get('time_filter')  # Optional

    # Reddit typically allows up to 100 items per request
    actual_limit = min(limit, 100)

    # Build request URL and parameters
    url = f"{REDDIT_BASE}/r/{subreddit}/hot.json"
    params = {
        'limit': actual_limit,
        'raw_json': 1,  # Prevent HTML entity encoding
    }

    # Pagination
    if cursor:
        params['after'] = cursor

    # Optional time filter
    if time_filter:
        params['t'] = time_filter

    logger.info(
        f"Fetching Reddit hot posts: subreddit=r/{subreddit}, limit={actual_limit}"
    )

    # Make API request with User-Agent (Reddit requires it)
    headers = {
        'User-Agent': 'TrendCrawler/1.0 (Culture-Flexible Trend Aggregator)'
    }

    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

    # Parse response
    items: List[CollectedItem] = []
    posts = data.get('data', {}).get('children', [])

    logger.info(f"Reddit API returned {len(posts)} posts")

    for rank, post_wrapper in enumerate(posts, start=1):
        post = post_wrapper.get('data', {})

        # Skip if not a post (e.g., promoted content)
        if post_wrapper.get('kind') != 't3':
            continue

        # ENHANCED: Filter NSFW content
        is_nsfw = post.get('over_18', False)
        if is_nsfw:
            logger.debug(f"Filtering NSFW post: {post.get('id', 'unknown')}")
            continue

        # Extract core fields
        post_id = post.get('id', '')
        title = post.get('title', 'Untitled')
        selftext = post.get('selftext', '')
        author = post.get('author', '[deleted]')
        subreddit_name = post.get('subreddit', subreddit)
        permalink = post.get('permalink', '')
        created_utc = post.get('created_utc', 0)

        # ENHANCED: Better URL determination logic
        # For self posts or media posts, use Reddit permalink
        # For external links, use the actual URL
        is_self = post.get('is_self', False)
        post_hint = post.get('post_hint', '')
        post_url = post.get('url', '')

        if is_self or post_hint in ['image', 'hosted:video', 'rich:video'] or \
           'i.redd.it' in post_url or 'v.redd.it' in post_url:
            url = f"{REDDIT_BASE}{permalink}"
        else:
            url = post_url if post_url else f"{REDDIT_BASE}{permalink}"

        # Convert created_utc to ISO8601
        published_at = None
        if created_utc:
            try:
                published_at = datetime.fromtimestamp(created_utc, tz=timezone.utc).isoformat()
            except (ValueError, OSError):
                pass

        # Extract engagement signals
        score = post.get('score', 0)
        num_comments = post.get('num_comments', 0)
        upvote_ratio = post.get('upvote_ratio', 0.0)

        # Calculate approximate upvotes/downvotes
        # upvote_ratio = upvotes / (upvotes + downvotes)
        # score = upvotes - downvotes
        if upvote_ratio > 0:
            upvotes = int(score / (2 * upvote_ratio - 1)) if upvote_ratio != 0.5 else score
        else:
            upvotes = 0

        # Build description (selftext or None)
        description = selftext[:1000] if selftext else None

        # ENHANCED: Extract additional metadata
        domain = post.get('domain', '')
        gilded = post.get('gilded', 0)  # Number of gold awards

        # Build collected item
        item: CollectedItem = {
            "external_id": post_id,
            "title": title,
            "description": description,
            "url": url,
            "locale": "en-US",  # Reddit content is primarily English
            "published_at": published_at,

            # CRITICAL: rank_position (from /tmp/t9)
            "rank_position": rank,

            # CRITICAL: engagement_signals (from /tmp/t9)
            # ENHANCED: Added gilded awards
            "engagement_signals": {
                "upvotes": upvotes,
                "score": score,  # Reddit's score (upvotes - downvotes)
                "comments": num_comments,
                "upvote_ratio": upvote_ratio,
                "gilded": gilded,  # Number of gold awards
            },

            # CRITICAL: raw_payload (from /tmp/t9)
            # ENHANCED: Store complete post data with extra metadata
            "raw_payload": {
                **post,
                # Extra metadata for easy access
                "_metadata": {
                    "subreddit": subreddit_name,
                    "is_self": is_self,
                    "post_hint": post_hint,
                    "domain": domain,
                    "author": author,
                }
            },
        }
        items.append(item)

    # Get next page cursor for pagination
    next_cursor = data.get('data', {}).get('after')

    logger.info(
        f"Collected {len(items)} Reddit posts. Next cursor: {next_cursor or 'None'}"
    )

    return items, next_cursor
