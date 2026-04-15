"""
Hacker News surface collector.

Collects top stories from Hacker News using the official Firebase API.
Uses a two-step approach: fetch story IDs, then fetch details concurrently.

Source: https://hacker-news.firebaseio.com/v0/topstories.json
Schedule: Every 20 minutes
Max items: 30

Surface type: ranking
Bucket: category_tech (technology news and discussions)

Migrated from: /home/tnnd/data/code/trend/trend_agent/collectors/hackernews.py
"""

import asyncio
import logging
import re
from typing import Optional, Tuple, List
from datetime import datetime, timezone

import httpx

from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient
from shared.human_behavior import HumanBrowsingSession
from shared.url_utils import extract_domain
from shared.comment_settings import get_top_comments_count

logger = logging.getLogger(__name__)

# Hacker News API URLs
TOP_STORIES_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{}.json"
HN_BASE_URL = "https://news.ycombinator.com/item?id={}"

# HTML entity map for comment text cleanup
_HTML_ENTITIES = {
    '&gt;': '>',
    '&lt;': '<',
    '&amp;': '&',
    '&#x27;': "'",
    '&#x2F;': '/',
    '&quot;': '"',
    '&nbsp;': ' ',
}


def _strip_html(text: str) -> str:
    """Strip HTML tags and decode common entities from HN comment text."""
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Decode common entities
    for entity, char in _HTML_ENTITIES.items():
        text = text.replace(entity, char)
    # Collapse whitespace
    return re.sub(r'\s+', ' ', text).strip()


async def fetch_story_comments(
    client: httpx.AsyncClient,
    kid_ids: List[int],
    limit: int,
) -> List[str]:
    """
    Fetch the top `limit` comments for a Hacker News story.

    HN stores top-level comment IDs in the story's `kids` field, ordered by
    HN's own ranking (best first). We fetch each comment individually using
    the Firebase API and strip HTML markup from the text.

    Args:
        client: HTTP client
        kid_ids: Top-level comment IDs from story['kids']
        limit: Maximum number of comments to return

    Returns:
        List of plain-text comment strings (up to 300 chars each), empty on failure
    """
    if not kid_ids or limit <= 0:
        return []

    # Fetch 2× the limit to account for deleted/empty comments, then trim.
    candidate_ids = kid_ids[:limit * 2]

    sem = asyncio.Semaphore(5)

    async def _fetch_one(cid: int) -> Optional[str]:
        async with sem:
            try:
                resp = await client.get(ITEM_URL.format(cid), timeout=8.0)
                resp.raise_for_status()
                data = resp.json()
                if not data or data.get('type') != 'comment':
                    return None
                if data.get('deleted') or data.get('dead'):
                    return None
                text = data.get('text', '').strip()
                if not text:
                    return None
                return _strip_html(text)[:300]
            except Exception as e:
                logger.debug(f"Failed to fetch HN comment {cid}: {e}")
                return None

    raw_results = await asyncio.gather(*[_fetch_one(cid) for cid in candidate_ids])

    comments = [r for r in raw_results if r][:limit]
    return comments


async def fetch_story(
    client: httpx.AsyncClient,
    story_id: int,
    rank: int,
    top_comments_count: int = 0,
) -> Optional[CollectedItem]:
    """
    Fetch a single Hacker News story by ID.

    Args:
        client: httpx async client
        story_id: Story ID from HN API
        rank: Position in top stories (1-based)
        top_comments_count: Number of top comments to fetch (0 = disabled)

    Returns:
        CollectedItem dict or None if fetch fails
    """
    try:
        response = await client.get(
            ITEM_URL.format(story_id),
            timeout=10.0
        )
        response.raise_for_status()
        data = response.json()

        if not data or not data.get("title"):
            logger.warning(f"HN story {story_id} has no title")
            return None

        # Extract story details
        title = data.get("title", "")

        # HN stories can have external URLs or be discussions (Ask HN, Show HN)
        # If no external URL, use HN discussion page
        url = data.get("url", HN_BASE_URL.format(story_id))

        text = data.get("text", "")  # For Ask HN, Show HN posts
        score = data.get("score", 0)
        descendants = data.get("descendants", 0)  # Comment count
        author = data.get("by", "")
        timestamp = data.get("time", 0)
        kid_ids: List[int] = data.get("kids", [])  # Top-level comment IDs

        # Convert Unix timestamp to ISO8601
        published_at = None
        if timestamp:
            try:
                published_at = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
            except (ValueError, OSError):
                pass

        # Build description from text if available (Ask HN / Show HN posts)
        description = text[:1000] if text else None

        # Fetch top comments if enabled
        top_comments: List[str] = []
        if top_comments_count > 0 and kid_ids:
            top_comments = await fetch_story_comments(client, kid_ids, top_comments_count)
            if top_comments:
                logger.debug(
                    f"Fetched {len(top_comments)} comments for HN story {story_id}"
                )

        # Build collected item
        item: CollectedItem = {
            "external_id": str(story_id),
            "title": title,
            "url": url,
            "locale": "en-US",
            "rank_position": rank,

            # Engagement signals
            "engagement_signals": {
                "score": score,  # HN points
                "comments": descendants,  # Number of comments
            },

            # Store complete HN story data
            "raw_payload": {
                **data,
                "_metadata": {
                    "story_id": story_id,
                    "type": data.get("type", "story"),
                    "hn_url": HN_BASE_URL.format(story_id),
                    "author": author,
                },
                "top_comments": top_comments,
            },
        }

        # Add optional fields
        if description:
            item["description"] = description

        if published_at:
            item["published_at"] = published_at

        return item

    except httpx.HTTPError as e:
        logger.warning(f"HTTP error fetching HN story {story_id}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Failed to fetch HN story {story_id}: {e}")
        return None


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect top stories from Hacker News.

    This collector uses a two-step approach:
    1. Fetch list of top story IDs
    2. Fetch details for each story concurrently (with concurrency limit)

    Config params (from TrendSurface.config_json):
        - max_stories: Maximum stories to fetch (default: 30)
        - timeout: Request timeout in seconds (default: 30)
        - max_concurrent: Max concurrent fetches (default: 10)
        - top_comments: Override number of comments to fetch per story (0 = disable).
                        Falls back to global SystemSettings[crawler.top_comments_per_post].

    Args:
        config: Surface configuration
        cursor: Not used (HN doesn't support pagination, always fetches latest top stories)
        limit: Maximum items to collect (default: 30)

    Returns:
        (items, next_cursor)

    Note:
        Cursor is always None since HN top stories list is dynamic and doesn't
        support pagination. We always fetch the latest top stories.

    Raises:
        httpx.HTTPError: On API request failures
    """
    max_stories = min(config.get('max_stories', 30), limit)
    timeout = config.get('timeout', 30.0)
    max_concurrent = config.get('max_concurrent', 10)

    # Resolve how many comments to fetch per story (0 = disabled)
    top_comments_count = await get_top_comments_count(config)

    logger.info(f"Collecting Hacker News top stories: limit={max_stories}, concurrent={max_concurrent}")

    # Use RateLimitedClient for resilience
    async with RateLimitedClient(timeout=timeout) as client:
        # Initialize human browsing session
        domain = extract_domain(TOP_STORIES_URL)
        human_session = await HumanBrowsingSession.from_config(client, domain)

        # Optional: maybe visit homepage before fetching
        await human_session.maybe_visit_homepage(TOP_STORIES_URL)

        try:
            # Step 1: Fetch top story IDs (request_role="direct" by default)
            response = await client.get(TOP_STORIES_URL)
            response.raise_for_status()
            story_ids = response.json()

            if not story_ids:
                logger.warning("No story IDs returned from Hacker News")
                return [], None

            # Limit to requested number of stories
            story_ids = story_ids[:max_stories]

            logger.info(f"Fetching {len(story_ids)} HN stories concurrently (max {max_concurrent} at once)")

            # Step 2: Fetch story details concurrently with semaphore limit
            # This prevents unconstrained fan-out that could overwhelm HN API
            semaphore = asyncio.Semaphore(max_concurrent)

            async def fetch_with_semaphore(story_id: int, rank: int):
                """Fetch story with concurrency control."""
                async with semaphore:
                    return await fetch_story(
                        client, story_id, rank, top_comments_count=top_comments_count
                    )

            tasks = [
                fetch_with_semaphore(story_id, rank)
                for rank, story_id in enumerate(story_ids, start=1)
            ]

            # Use gather to fetch all stories concurrently
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Filter out None and exception results
            items: List[CollectedItem] = []
            for result in results:
                if isinstance(result, dict):
                    items.append(result)
                elif isinstance(result, Exception):
                    logger.warning(f"Story fetch failed with exception: {result}")

            logger.info(f"Successfully collected {len(items)} stories from Hacker News")

            # HN doesn't support pagination, so cursor is always None
            return items, None

        except httpx.HTTPError as e:
            logger.error(f"HTTP error fetching Hacker News data: {e}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Error collecting Hacker News data: {e}", exc_info=True)
            raise
