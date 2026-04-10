"""
Base RSS collector utilities for news sources.

This module provides reusable functions for RSS-based collectors,
reducing code duplication and standardizing RSS handling across
multiple news sources.

Adapted from: /home/tnnd/data/code/trend/trend_agent/collectors/base_rss.py
Target interface: async def collect(config, cursor, limit) -> (items, cursor)
"""

import logging
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any
from io import BytesIO

import feedparser
from bs4 import BeautifulSoup

from .adapters import normalize_rss_entry
from .collector_interface import CollectedItem
from shared.http_client import RateLimitedClient
from shared.human_behavior import HumanBrowsingSession
from shared.url_utils import extract_domain

logger = logging.getLogger(__name__)


def clean_html(html_content: str) -> str:
    """
    Remove HTML tags and clean text content.

    Args:
        html_content: HTML string

    Returns:
        Plain text with HTML removed

    Example:
        >>> clean_html('<p>Hello <b>world</b>!</p>')
        'Hello world!'
    """
    if not html_content:
        return ""

    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        text = soup.get_text(separator=' ', strip=True)
        return text
    except Exception as e:
        logger.warning(f"Error cleaning HTML: {e}")
        return html_content


def parse_timestamp(entry: feedparser.FeedParserDict) -> Optional[datetime]:
    """
    Extract timestamp from RSS feed entry.

    Tries published_parsed first, then updated_parsed, then falls back to None.

    Args:
        entry: RSS feed entry from feedparser

    Returns:
        datetime object or None if parsing fails

    Example:
        >>> entry = {'published_parsed': (2024, 1, 15, 12, 30, 0, 0, 15, 0)}
        >>> ts = parse_timestamp(entry)
        >>> ts.year
        2024
    """
    # Try published_parsed first
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        try:
            # published_parsed is a time.struct_time
            return datetime(*entry.published_parsed[:6])
        except (TypeError, ValueError, AttributeError):
            pass

    # Try updated_parsed
    if hasattr(entry, 'updated_parsed') and entry.updated_parsed:
        try:
            return datetime(*entry.updated_parsed[:6])
        except (TypeError, ValueError, AttributeError):
            pass

    # No timestamp available
    logger.debug(f"Could not parse timestamp for: {entry.get('title', 'Unknown')}")
    return None


def parse_rss_entry(
    entry: feedparser.FeedParserDict,
    rank: int,
    source: str,
    locale: str = "en-US",
    feed_url: str = ""
) -> Optional[CollectedItem]:
    """
    Parse an RSS entry into a CollectedItem.

    This is a more detailed version of normalize_rss_entry that includes
    HTML cleaning and better metadata extraction.

    Args:
        entry: RSS feed entry from feedparser
        rank: Position in the feed (1-based)
        source: Source identifier (e.g., 'bbc', 'reuters')
        locale: Locale code (default: 'en-US')
        feed_url: RSS feed URL for metadata

    Returns:
        CollectedItem dict or None if parsing fails

    Example:
        >>> import feedparser
        >>> feed = feedparser.parse('https://feeds.bbci.co.uk/news/rss.xml')
        >>> item = parse_rss_entry(feed.entries[0], rank=1, source='bbc')
        >>> item['rank_position']
        1
    """
    try:
        # Extract basic fields
        title = entry.get('title', '').strip()
        url = entry.get('link', '')

        if not title or not url:
            logger.warning(f"Skipping RSS entry: missing title or URL")
            return None

        # Extract and clean description
        raw_description = entry.get('summary', '') or entry.get('description', '')
        description = clean_html(raw_description) if raw_description else None

        # Get unique ID (use link as fallback)
        source_id = entry.get('id', None) or entry.get('guid', None) or url

        # Extract timestamp
        published_at = parse_timestamp(entry)

        # Extract author
        author = entry.get('author', None)

        # Extract tags
        tags = [tag.get('term', '') for tag in entry.get('tags', [])]

        # Build metadata
        metadata = {
            'feed_url': feed_url,
            'entry_id': entry.get('id', ''),
            'tags': tags,
        }
        if author:
            metadata['author'] = author

        # Build collected item
        item: CollectedItem = {
            "external_id": str(source_id),
            "title": title,
            "url": url,
            "locale": locale,
            "rank_position": rank,

            # RSS feeds typically don't have engagement metrics
            "engagement_signals": {},

            # Store complete RSS entry
            "raw_payload": {
                **dict(entry),
                **metadata,
                "source": source,
            },
        }

        # Add optional description
        if description:
            item["description"] = description[:2000]  # Truncate long descriptions

        # Add published_at if available
        if published_at:
            item["published_at"] = published_at.isoformat()

        return item

    except Exception as e:
        logger.warning(f"Failed to parse RSS entry: {e}")
        return None


async def collect_rss_feed(
    rss_url: str,
    source: str,
    config: dict,
    cursor: Optional[str],
    limit: int,
    locale: str = "en-US",
    headers: Optional[dict] = None,
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect items from an RSS feed.

    This is a reusable function that can be called by specific RSS collectors.
    It handles feed parsing, entry processing, and pagination.

    RSS feeds typically don't support cursor-based pagination, so we use
    cursor to track the last seen entry ID to avoid duplicates.

    Args:
        rss_url: RSS feed URL
        source: Source identifier (e.g., 'bbc', 'reuters')
        config: Surface configuration (can override locale)
        cursor: Last seen entry ID (for deduplication)
        limit: Maximum items to collect
        locale: Default locale code
        headers: Optional extra HTTP headers (e.g. User-Agent override for
                 feeds that block the default python-httpx UA)

    Returns:
        Tuple of (items, next_cursor)

    Raises:
        Exception: On feed parsing errors

    Example:
        >>> items, cursor = await collect_rss_feed(
        ...     rss_url='https://feeds.bbci.co.uk/news/rss.xml',
        ...     source='bbc',
        ...     config={},
        ...     cursor=None,
        ...     limit=40
        ... )
        >>> len(items) <= 40
        True
    """
    # Allow config to override locale
    locale = config.get('locale', locale)

    logger.info(f"Collecting from RSS feed: {rss_url} (source={source}, limit={limit})")

    try:
        # Fetch RSS feed via RateLimitedClient for resilience
        # This gives us rate limiting, circuit breaker, and metrics
        async with RateLimitedClient(timeout=30.0) as client:
            # Initialize human browsing session
            domain = extract_domain(rss_url)
            human_session = await HumanBrowsingSession.from_config(client, domain)

            # Optional: maybe visit homepage before fetching feed
            await human_session.maybe_visit_homepage(rss_url)

            # Fetch RSS feed — pass optional headers (e.g. custom User-Agent)
            response = await client.get(rss_url, headers=headers or {})
            response.raise_for_status()

            # Parse RSS feed content
            # feedparser can parse from string or file-like object
            feed = feedparser.parse(BytesIO(response.content))

        # Check for feed errors
        if feed.get('bozo', False):
            bozo_exception = feed.get('bozo_exception', '')
            logger.warning(f"RSS feed may have issues: {bozo_exception}")
            # Continue anyway - often still usable

        # Get feed entries
        entries = feed.get('entries', [])
        logger.info(f"RSS feed returned {len(entries)} entries")

        if not entries:
            logger.warning(f"No entries found in RSS feed: {rss_url}")
            return [], None

        # Process entries
        items: List[CollectedItem] = []
        last_entry_id = cursor

        for rank, entry in enumerate(entries[:limit], start=1):
            # Parse entry
            item = parse_rss_entry(
                entry=entry,
                rank=rank,
                source=source,
                locale=locale,
                feed_url=rss_url
            )

            if item:
                items.append(item)
                # Track last entry ID for next run
                last_entry_id = item['external_id']

            # Stop if we've reached the limit
            if len(items) >= limit:
                break

        logger.info(f"Collected {len(items)} items from {source}")

        # Return items and new cursor (last entry ID)
        # Note: RSS feeds are typically sorted by date, so we use the first
        # entry ID as the cursor to detect new items in next run
        next_cursor = items[0]['external_id'] if items else cursor

        return items, next_cursor

    except Exception as e:
        logger.error(f"Error fetching RSS feed {rss_url}: {e}", exc_info=True)
        raise


async def collect_rss_with_etag(
    rss_url: str,
    source: str,
    config: dict,
    cursor: Optional[str],
    limit: int,
    locale: str = "en-US"
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect items from an RSS feed with ETag support.

    This variant uses HTTP ETag headers to avoid re-downloading unchanged feeds.
    The cursor stores the ETag from the previous fetch.

    Args:
        rss_url: RSS feed URL
        source: Source identifier
        config: Surface configuration
        cursor: ETag from previous fetch
        limit: Maximum items to collect
        locale: Default locale code

    Returns:
        Tuple of (items, next_cursor)

    Note:
        If the feed hasn't changed (304 Not Modified), returns empty list
        with the same cursor.
    """
    logger.info(f"Collecting from RSS feed with ETag: {rss_url}")

    try:
        # Fetch via RateLimitedClient with automatic ETag support
        # The client handles If-None-Match headers automatically
        async with RateLimitedClient(timeout=30.0) as client:
            # Initialize human browsing session
            domain = extract_domain(rss_url)
            human_session = await HumanBrowsingSession.from_config(client, domain)

            # Optional: maybe visit homepage before fetching feed
            await human_session.maybe_visit_homepage(rss_url)

            # Fetch RSS feed (request_role="direct" by default)
            response = await client.get(rss_url)

            # Check if feed was not modified (304)
            if response.status_code == 304:
                logger.info(f"RSS feed not modified (304): {rss_url}")
                return [], cursor  # Return same cursor

            response.raise_for_status()

            # Parse RSS feed content
            feed = feedparser.parse(BytesIO(response.content))

        # Get new ETag for next request (from response headers)
        new_etag = response.headers.get('ETag')

        # Process entries normally
        entries = feed.get('entries', [])
        items: List[CollectedItem] = []

        for rank, entry in enumerate(entries[:limit], start=1):
            item = parse_rss_entry(
                entry=entry,
                rank=rank,
                source=source,
                locale=locale,
                feed_url=rss_url
            )
            if item:
                items.append(item)

        logger.info(f"Collected {len(items)} items from {source}")

        # Return new ETag as cursor
        next_cursor = new_etag if new_etag else (items[0]['external_id'] if items else cursor)

        return items, next_cursor

    except Exception as e:
        logger.error(f"Error fetching RSS feed {rss_url}: {e}", exc_info=True)
        raise
