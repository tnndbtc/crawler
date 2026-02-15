"""
Google News surface collector.

Fetches top headlines from Google News RSS feed.
Google News aggregates headlines from multiple sources, so this collector
extracts the primary article from each entry.

Source: https://news.google.com/rss
Schedule: Every 15 minutes
Max items: 50

Surface type: news
Bucket: region_local (major news aggregator)

Migrated from: /home/tnnd/data/code/trend/trend_agent/collectors/google_news.py
"""

import logging
from typing import Optional, Tuple, List

import feedparser
from bs4 import BeautifulSoup

from .base_rss import parse_timestamp, clean_html
from .collector_interface import CollectedItem

logger = logging.getLogger(__name__)

# Google News RSS feed URL
GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss"


def extract_first_article_info(summary_html: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract the first article's title from Google News summary HTML.

    Google News RSS summary contains aggregated headlines from multiple sources.
    This function parses the HTML to extract just the first article's information.

    Args:
        summary_html: HTML content from Google News RSS summary field

    Returns:
        Tuple of (article_title, source_name) or (None, None) if not found
    """
    if not summary_html:
        return None, None

    try:
        soup = BeautifulSoup(summary_html, 'html.parser')

        # Find first <li> which contains the primary article
        first_li = soup.find('li')
        if not first_li:
            return None, None

        # Extract the article title from the first link
        first_link = first_li.find('a')
        if first_link:
            article_title = first_link.get_text(strip=True)
        else:
            article_title = None

        # Extract source name from the <font> tag
        source_font = first_li.find('font')
        if source_font:
            source_name = source_font.get_text(strip=True)
        else:
            source_name = None

        return article_title, source_name

    except Exception as e:
        logger.warning(f"Failed to parse Google News summary: {e}")
        return None, None


def parse_google_news_entry(
    entry: feedparser.FeedParserDict,
    rank: int
) -> Optional[CollectedItem]:
    """
    Parse a Google News RSS entry into a CollectedItem.

    Args:
        entry: RSS feed entry from feedparser
        rank: Position in the feed (1-based)

    Returns:
        CollectedItem dict or None if parsing fails
    """
    try:
        # Extract basic fields
        title = entry.get('title', '').strip()
        url = entry.get('link', '')

        if not title or not url:
            logger.warning(f"Skipping Google News entry: missing title or URL")
            return None

        # Extract article info from summary
        summary_html = entry.get("summary", "")
        article_title, source_name = extract_first_article_info(summary_html)

        # Use extracted article title as description
        description = article_title if article_title else ""

        # Get unique ID (use link as fallback)
        source_id = entry.get('id', None) or entry.get('guid', None) or url

        # Extract timestamp
        published_at = parse_timestamp(entry)

        # Build collected item
        item: CollectedItem = {
            "external_id": str(source_id),
            "title": title,
            "url": url,
            "locale": "en-US",  # Google News is primarily English
            "rank_position": rank,

            # RSS feeds don't have engagement metrics
            "engagement_signals": {},

            # Store complete RSS entry
            "raw_payload": {
                **dict(entry),
                "source": "google_news",
                "original_source": source_name,
                "feed_url": GOOGLE_NEWS_RSS_URL,
            },
        }

        # Add description if available
        if description:
            item["description"] = description[:2000]

        # Add published_at if available
        if published_at:
            item["published_at"] = published_at.isoformat()

        return item

    except Exception as e:
        logger.warning(f"Failed to parse Google News entry: {e}")
        return None


async def collect(
    config: dict,
    cursor: Optional[str],
    limit: int
) -> Tuple[List[CollectedItem], Optional[str]]:
    """
    Collect top headlines from Google News RSS feed.

    Config params (from TrendSurface.config_json):
        - locale: Locale code (default: 'en-US')
        - rss_url: Override default RSS URL (optional)
        - topic: Google News topic filter (optional: world, nation, business, technology, entertainment, sports, science, health)

    Args:
        config: Surface configuration
        cursor: Last seen entry ID (for deduplication)
        limit: Maximum items to collect (default: 50)

    Returns:
        (items, next_cursor)

    Raises:
        Exception: On RSS feed parsing errors
    """
    # Allow config to override RSS URL or add topic filter
    rss_url = config.get('rss_url', GOOGLE_NEWS_RSS_URL)
    topic = config.get('topic')

    # Add topic filter if specified
    if topic:
        rss_url = f"https://news.google.com/rss/topics/{topic.upper()}"

    logger.info(f"Collecting Google News: limit={limit}, url={rss_url}")

    try:
        # Parse RSS feed
        feed = feedparser.parse(rss_url)

        # Check for feed errors
        if feed.get('bozo', False):
            bozo_exception = feed.get('bozo_exception', '')
            logger.warning(f"Google News RSS feed may have issues: {bozo_exception}")

        # Get feed entries
        entries = feed.get('entries', [])
        logger.info(f"Google News RSS feed returned {len(entries)} entries")

        if not entries:
            logger.warning(f"No entries found in Google News RSS feed")
            return [], None

        # Process entries
        items: List[CollectedItem] = []

        for rank, entry in enumerate(entries[:limit], start=1):
            item = parse_google_news_entry(entry=entry, rank=rank)

            if item:
                items.append(item)

            # Stop if we've reached the limit
            if len(items) >= limit:
                break

        logger.info(f"Collected {len(items)} items from Google News")

        # Return new cursor (first entry ID to detect new items)
        next_cursor = items[0]['external_id'] if items else cursor

        return items, next_cursor

    except Exception as e:
        logger.error(f"Error fetching Google News RSS feed: {e}", exc_info=True)
        raise
