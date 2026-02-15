"""
Adapter utilities for migrating collectors from the source project.

This module provides transformation functions to convert data models from
the source project (/home/tnnd/data/code/trend) to the target project's
expected formats.

Source models (Pydantic):
- RawItem: Rich Pydantic model with typed fields
- Metrics: Engagement metrics model

Target format (TypedDict):
- CollectedItem: Simple dict with required fields
- EngagementSignals: Simple dict for engagement data
"""

from typing import Dict, Any, Optional
from datetime import datetime


def metrics_to_engagement_signals(
    upvotes: int = 0,
    downvotes: int = 0,
    comments: int = 0,
    shares: int = 0,
    views: int = 0,
    score: float = 0.0,
    **kwargs
) -> Dict[str, Any]:
    """
    Convert Metrics model to engagement_signals dict.

    The source project uses a Pydantic Metrics model, while the target
    project expects a simple dict.

    Args:
        upvotes: Number of upvotes/likes
        downvotes: Number of downvotes (if available)
        comments: Number of comments/replies
        shares: Number of shares/retweets
        views: Number of views
        score: Composite engagement score
        **kwargs: Additional platform-specific metrics

    Returns:
        Dict suitable for CollectedItem['engagement_signals']

    Example:
        >>> metrics = Metrics(upvotes=100, comments=50, score=95.5)
        >>> signals = metrics_to_engagement_signals(**metrics.dict())
        >>> signals['upvotes']
        100
    """
    signals: Dict[str, Any] = {}

    # Include all non-zero standard metrics
    if upvotes > 0:
        signals['upvotes'] = upvotes
    if downvotes > 0:
        signals['downvotes'] = downvotes
    if comments > 0:
        signals['comments'] = comments
    if shares > 0:
        signals['shares'] = shares
    if views > 0:
        signals['views'] = views
    if score > 0:
        signals['score'] = score

    # Include any additional platform-specific metrics
    for key, value in kwargs.items():
        if key not in signals and value is not None:
            signals[key] = value

    return signals


def datetime_to_iso8601(dt: Optional[datetime]) -> Optional[str]:
    """
    Convert datetime to ISO8601 string.

    Args:
        dt: Python datetime object (aware or naive)

    Returns:
        ISO8601 formatted string, or None if input is None

    Example:
        >>> from datetime import datetime, timezone
        >>> dt = datetime(2024, 1, 15, 12, 30, 0, tzinfo=timezone.utc)
        >>> datetime_to_iso8601(dt)
        '2024-01-15T12:30:00+00:00'
    """
    if dt is None:
        return None
    return dt.isoformat()


def raw_item_to_collected_item(
    source: str,
    source_id: str,
    url: str,
    title: str,
    description: Optional[str] = None,
    content: Optional[str] = None,
    author: Optional[str] = None,
    published_at: Optional[datetime] = None,
    collected_at: Optional[datetime] = None,
    metrics: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    language: str = "en",
    rank_position: int = 1,
    locale: Optional[str] = None,
    raw_payload: Optional[Dict[str, Any]] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Convert RawItem model to CollectedItem dict.

    The source project uses a Pydantic RawItem model with rich typing,
    while the target project expects a simple dict following the
    CollectedItem TypedDict schema.

    Args:
        source: Source type (e.g., 'reddit', 'bbc', 'google_news')
        source_id: ID from the source system
        url: Content URL (can be HttpUrl or str)
        title: Item title
        description: Optional description/snippet
        content: Optional full content
        author: Optional author name
        published_at: Publication datetime
        collected_at: Collection datetime
        metrics: Metrics dict or Metrics model
        metadata: Additional metadata
        language: ISO 639-1 language code
        rank_position: Position in ranking (1=top)
        locale: Locale code (e.g., 'en-US'). If not provided, derived from language
        raw_payload: Complete platform response
        **kwargs: Additional fields to include

    Returns:
        Dict suitable for CollectedItem

    Example:
        >>> from datetime import datetime, timezone
        >>> item = raw_item_to_collected_item(
        ...     source="reddit",
        ...     source_id="abc123",
        ...     url="https://reddit.com/r/all/comments/abc123",
        ...     title="Breaking News",
        ...     published_at=datetime.now(timezone.utc),
        ...     metrics={"upvotes": 1000, "comments": 50},
        ...     rank_position=1,
        ...     raw_payload={"id": "abc123", "title": "Breaking News"}
        ... )
        >>> item['title']
        'Breaking News'
        >>> item['rank_position']
        1
    """
    # Convert URL to string if it's a Pydantic HttpUrl
    url_str = str(url)

    # Derive locale from language if not provided
    if locale is None:
        # Common language -> locale mappings
        language_to_locale = {
            'en': 'en-US',
            'ja': 'ja-JP',
            'es': 'es-ES',
            'fr': 'fr-FR',
            'de': 'de-DE',
            'zh': 'zh-CN',
            'ko': 'ko-KR',
            'ar': 'ar-SA',
            'pt': 'pt-BR',
            'ru': 'ru-RU',
            'it': 'it-IT',
        }
        locale = language_to_locale.get(language, f'{language}-{language.upper()}')

    # Build engagement signals from metrics
    engagement_signals = {}
    if metrics:
        engagement_signals = metrics_to_engagement_signals(**metrics)

    # Build the collected item
    collected_item: Dict[str, Any] = {
        # Required fields
        "title": title,
        "url": url_str,
        "locale": locale,
        "raw_payload": raw_payload or {},

        # Critical fields
        "rank_position": rank_position,
        "engagement_signals": engagement_signals,

        # Optional fields
        "external_id": source_id,
    }

    # Add description if available
    if description:
        collected_item["description"] = description

    # Add published_at if available (convert to ISO8601)
    if published_at:
        collected_item["published_at"] = datetime_to_iso8601(published_at)

    # Include any additional fields from kwargs
    for key, value in kwargs.items():
        if key not in collected_item and value is not None:
            collected_item[key] = value

    return collected_item


def normalize_rss_entry(entry: Any, rank: int, source: str) -> Dict[str, Any]:
    """
    Normalize an RSS feed entry to CollectedItem format.

    This is a convenience function for RSS-based collectors. It handles
    the common RSS feed entry structure from feedparser.

    Args:
        entry: feedparser entry object
        rank: Position in the feed (1-based)
        source: Source identifier (e.g., 'bbc', 'reuters')

    Returns:
        Dict suitable for CollectedItem

    Example:
        >>> import feedparser
        >>> feed = feedparser.parse('https://feeds.bbci.co.uk/news/rss.xml')
        >>> item = normalize_rss_entry(feed.entries[0], rank=1, source='bbc')
        >>> item['title']
        'Breaking News Title'
    """
    # Extract common RSS fields
    title = getattr(entry, 'title', 'Untitled')
    link = getattr(entry, 'link', '')
    description = getattr(entry, 'summary', None) or getattr(entry, 'description', None)

    # Extract ID (varies by feed)
    entry_id = getattr(entry, 'id', None) or getattr(entry, 'guid', None) or link

    # Extract published date
    published_at = None
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        try:
            from time import mktime
            timestamp = mktime(entry.published_parsed)
            published_at = datetime.fromtimestamp(timestamp)
        except (ValueError, OverflowError):
            pass
    elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
        try:
            from time import mktime
            timestamp = mktime(entry.updated_parsed)
            published_at = datetime.fromtimestamp(timestamp)
        except (ValueError, OverflowError):
            pass

    # Extract author
    author = getattr(entry, 'author', None)

    # Build collected item
    return raw_item_to_collected_item(
        source=source,
        source_id=str(entry_id),
        url=link,
        title=title,
        description=description,
        author=author,
        published_at=published_at,
        rank_position=rank,
        locale='en-US',  # Most RSS feeds are English, can be overridden
        raw_payload=dict(entry),  # Store complete entry
        engagement_signals={},  # RSS feeds typically don't have engagement metrics
    )


def build_raw_payload(original_data: Any, **extra_fields) -> Dict[str, Any]:
    """
    Build a raw_payload dict from various data sources.

    The target project requires raw_payload to be a complete dict
    containing all original platform data.

    Args:
        original_data: Original platform data (dict, object, etc.)
        **extra_fields: Additional fields to include

    Returns:
        Dict suitable for raw_payload

    Example:
        >>> data = {"id": "123", "title": "News"}
        >>> payload = build_raw_payload(data, source="bbc", collected_at="2024-01-15")
        >>> payload['id']
        '123'
        >>> payload['source']
        'bbc'
    """
    payload: Dict[str, Any] = {}

    # Convert original data to dict if possible
    if isinstance(original_data, dict):
        payload.update(original_data)
    elif hasattr(original_data, 'dict'):
        # Pydantic model
        payload.update(original_data.dict())
    elif hasattr(original_data, '__dict__'):
        # Generic object
        payload.update(vars(original_data))

    # Add extra fields
    payload.update(extra_fields)

    return payload
