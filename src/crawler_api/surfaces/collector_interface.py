"""
Surface collector interface and utilities.

From REQUIREMENTS-MASTER.md:
- Collectors are async functions that fetch trending items
- Each collector must follow a standard signature
- Items must include rank_position, engagement_signals, raw_payload
- Translation NEVER blocks collection

Product constraint (from /tmp/t9):
- rank_position is MORE important than timestamps
- engagement_signals must be captured
- raw_payload must ALWAYS be stored (never throw away data)
"""

from typing import Optional, Protocol, TypedDict, Any, Tuple, List
from datetime import datetime


class EngagementSignals(TypedDict, total=False):
    """
    Platform engagement metrics.

    CRITICAL (from /tmp/t9):
    Always capture when available - helps future feed ranking.

    Common fields (adapt per platform):
    - upvotes: Reddit upvotes, likes, etc.
    - comments: Number of comments/replies
    - views: View count
    - shares: Share/retweet count
    - score: Composite score (if platform provides)
    """
    upvotes: int
    comments: int
    views: int
    shares: int
    score: float
    # Platform-specific fields can be added


class CollectedItem(TypedDict, total=False):
    """
    Standard format for collected trend items.

    From REQUIREMENTS-MASTER.md collector interface.

    Product constraints (from /tmp/t9):
    - rank_position > timestamps in importance
    - engagement_signals MUST be captured
    - raw_payload MUST be complete (never throw away data)
    """
    # Required fields
    title: str                          # Original title
    url: str                            # Link to content
    locale: str                         # Locale code (e.g., 'en-US', 'ja-JP')
    raw_payload: dict                   # Complete platform response (REQUIRED!)

    # Critical fields (from /tmp/t9)
    rank_position: int                  # Position in ranking (1=top) - IMPORTANT!
    engagement_signals: EngagementSignals  # Upvotes, views, etc. - IMPORTANT!

    # Optional fields
    external_id: str                    # Platform's unique ID
    description: str                    # Description/snippet
    published_at: str                   # ISO8601 timestamp (less important than rank)


class SurfaceCollector(Protocol):
    """
    Protocol (interface) for surface collectors.

    All collectors must implement this async function signature.

    Product rule (from /tmp/t9):
    - Collection must be FAST (translation happens later)
    - Always capture rank_position when available
    - Always include engagement_signals
    - Always store complete raw_payload
    """

    async def __call__(
        self,
        config: dict,
        cursor: Optional[str],
        limit: int
    ) -> Tuple[List[CollectedItem], Optional[str]]:
        """
        Collect trending items from a surface.

        Args:
            config: Surface-specific configuration from TrendSurface.config_json
            cursor: Pagination cursor from last run (TrendSurface.last_cursor)
            limit: Maximum items to collect (TrendSurface.max_items_per_run)

        Returns:
            Tuple of:
            - List of collected items (following CollectedItem schema)
            - Next cursor for pagination (or None if exhausted)

        Example implementation:
            ```python
            async def collect(config, cursor, limit):
                items = []
                for i, post in enumerate(fetch_reddit_hot(limit), start=1):
                    items.append({
                        "external_id": post.id,
                        "title": post.title,
                        "description": post.selftext,
                        "url": post.url,
                        "locale": "en-US",
                        "published_at": post.created_utc.isoformat(),

                        # CRITICAL: rank_position
                        "rank_position": i,

                        # CRITICAL: engagement_signals
                        "engagement_signals": {
                            "upvotes": post.score,
                            "comments": post.num_comments,
                        },

                        # CRITICAL: raw_payload (complete data)
                        "raw_payload": post.to_dict()
                    })

                return items, None  # No pagination in this example
            ```

        Raises:
            Exception: On collection errors (logged by worker)
        """
        ...
