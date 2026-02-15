"""
Twitter/X Trending surface collector.

⚠️ PLACEHOLDER - REQUIRES TWITTER API KEY ⚠️

This collector is ready to use but requires a Twitter API v2 Bearer Token.

Fetches trending topics and high-engagement tweets from Twitter/X.

Surface type: trending
Bucket: rising (new content gaining traction)

Requirements:
- Twitter API v2 Bearer Token (set in TWITTER_BEARER_TOKEN environment variable)
- Get your API key from: https://developer.twitter.com/en/portal/dashboard

API Costs:
- Free tier: 500,000 tweets/month
- Basic tier ($100/month): 10M tweets/month
- Recommended: Basic tier for production use

Migrated from: /home/tnnd/data/code/trend/trend_agent/ingestion/plugins/twitter.py
"""

import os
import logging
from typing import Optional, Tuple, List
from datetime import datetime, timedelta, timezone

import httpx

from .collector_interface import CollectedItem

logger = logging.getLogger(__name__)

# Twitter API v2 endpoints
TWITTER_API_BASE = "https://api.twitter.com/2"
TWITTER_SEARCH_URL = f"{TWITTER_API_BASE}/tweets/search/recent"


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect trending tweets from Twitter/X using API v2.

    ⚠️ REQUIRES: TWITTER_BEARER_TOKEN environment variable

    This collector searches for high-engagement tweets from recent hours,
    focusing on content that's gaining traction.

    Config params (from TrendSurface.config_json):
        - query: Twitter search query (default: high engagement filter)
        - locale: Locale code (default: 'en-US')
        - min_engagement: Minimum likes+retweets to consider (default: 100)
        - hours_back: How many hours back to search (default: 6)

    Args:
        config: Surface configuration
        cursor: Pagination token from Twitter API
        limit: Maximum items to collect (max: 100 per request)

    Returns:
        (items, next_cursor)

    Raises:
        ValueError: If TWITTER_BEARER_TOKEN is not set
        httpx.HTTPError: On API request failures

    Example config:
        {
            "query": "(#breaking OR #trending) -is:retweet -is:reply",
            "locale": "en-US",
            "min_engagement": 100,
            "hours_back": 6
        }
    """
    # Check for API key
    bearer_token = os.environ.get('TWITTER_BEARER_TOKEN')
    if not bearer_token:
        raise ValueError(
            "❌ TWITTER_BEARER_TOKEN environment variable not set.\n"
            "\n"
            "To use this collector, you need a Twitter API v2 Bearer Token:\n"
            "1. Go to: https://developer.twitter.com/en/portal/dashboard\n"
            "2. Create a new project and app\n"
            "3. Generate a Bearer Token\n"
            "4. Set environment variable: export TWITTER_BEARER_TOKEN='your-token'\n"
            "\n"
            "API Pricing:\n"
            "- Free tier: 500K tweets/month\n"
            "- Basic ($100/month): 10M tweets/month\n"
            "- Pro ($5000/month): 1M tweets/month + advanced features\n"
        )

    # Parse config
    locale = config.get('locale', 'en-US')
    min_engagement = config.get('min_engagement', 100)
    hours_back = config.get('hours_back', 6)

    # Build search query
    # Default: High engagement, no retweets/replies, recent
    base_query = config.get('query', '-is:retweet -is:reply has:media')

    # Add time constraint
    start_time = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()

    # Twitter API max is 100 per request
    actual_limit = min(limit, 100)

    # Build request parameters
    params = {
        'query': base_query,
        'start_time': start_time,
        'max_results': actual_limit,
        'tweet.fields': 'created_at,public_metrics,author_id,lang,entities',
        'expansions': 'author_id',
        'user.fields': 'username,name,verified',
    }

    # Pagination
    if cursor:
        params['next_token'] = cursor

    logger.info(f"Fetching Twitter/X trending tweets: limit={actual_limit}")

    # Make API request
    headers = {
        'Authorization': f'Bearer {bearer_token}',
        'User-Agent': 'TrendCrawler/1.0 (Culture-Flexible Trend Aggregator)',
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            TWITTER_SEARCH_URL,
            params=params,
            headers=headers
        )
        response.raise_for_status()
        data = response.json()

    # Parse response
    items: List[CollectedItem] = []
    tweets = data.get('data', [])
    users = {u['id']: u for u in data.get('includes', {}).get('users', [])}

    logger.info(f"Twitter API returned {len(tweets)} tweets")

    for rank, tweet in enumerate(tweets, start=1):
        tweet_id = tweet['id']
        text = tweet.get('text', '')
        created_at = tweet.get('created_at')
        author_id = tweet.get('author_id', '')
        lang = tweet.get('lang', 'en')

        # Get author info
        author = users.get(author_id, {})
        username = author.get('username', 'unknown')
        author_name = author.get('name', 'Unknown')

        # Extract engagement metrics
        metrics = tweet.get('public_metrics', {})
        likes = metrics.get('like_count', 0)
        retweets = metrics.get('retweet_count', 0)
        replies = metrics.get('reply_count', 0)
        quotes = metrics.get('quote_count', 0)

        # Calculate total engagement
        total_engagement = likes + retweets + replies + quotes

        # Filter by minimum engagement
        if total_engagement < min_engagement:
            continue

        # Extract hashtags
        entities = tweet.get('entities', {})
        hashtags = [tag['tag'] for tag in entities.get('hashtags', [])]

        # Build URL
        url = f"https://twitter.com/{username}/status/{tweet_id}"

        # Build collected item
        item: CollectedItem = {
            "external_id": tweet_id,
            "title": text[:200],  # Use first 200 chars as title
            "description": text if len(text) > 200 else None,
            "url": url,
            "locale": locale,
            "published_at": created_at,
            "rank_position": rank,

            # Engagement signals
            "engagement_signals": {
                "upvotes": likes,  # Map likes to upvotes
                "shares": retweets + quotes,
                "comments": replies,
                "score": float(total_engagement),
            },

            # Store complete tweet data
            "raw_payload": {
                **tweet,
                "_metadata": {
                    "author_username": username,
                    "author_name": author_name,
                    "hashtags": hashtags,
                    "total_engagement": total_engagement,
                    "lang": lang,
                }
            },
        }
        items.append(item)

    # Get next page token
    next_cursor = data.get('meta', {}).get('next_token')

    logger.info(f"Collected {len(items)} tweets. Next cursor: {next_cursor or 'None'}")

    return items, next_cursor
