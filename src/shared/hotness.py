"""
Hotness scoring utilities for selective translation.

Computes a platform-agnostic "hotness" score combining:
1. Recency decay (exponential time decay)
2. Engagement signals (log-scaled to prevent outliers)

Formula:
    hotness = recency_decay × log_engagement_weight × 100

    recency_decay = exp(-λ × hours_since_collected)
        where λ = 0.05 (half-life ~14 hours)

    log_engagement = log10(1 + weighted_engagement)

    weighted_engagement =
        upvotes×1.0 + comments×2.0 + views×0.1 + shares×3.0 +
        (100 - rank_position)×10

Properties:
- Time decay prevents stale content from staying "hot"
- Log scale prevents viral outliers from dominating
- Platform-agnostic (works with Reddit, HN, YouTube, etc.)
- Typical range: 0-1000

Usage:
    from shared.hotness import compute_hotness
    from crawler_admin.models import TrendItem

    item = TrendItem.objects.get(id=123)
    score = compute_hotness(item)
    item.hotness = score
    item.save()
"""

import math
import logging
from datetime import datetime, timezone as dt_timezone
from typing import Optional

logger = logging.getLogger(__name__)


# Hotness computation constants
RECENCY_LAMBDA = 0.05  # Decay rate (half-life ~14 hours)
HOTNESS_SCALE = 100.0  # Final scaling factor

# Engagement weight multipliers
WEIGHT_UPVOTES = 1.0
WEIGHT_COMMENTS = 2.0  # Comments more valuable (active engagement)
WEIGHT_VIEWS = 0.1     # Views less valuable (passive engagement)
WEIGHT_SHARES = 3.0    # Shares most valuable (viral potential)
WEIGHT_RANK = 10.0     # Rank position weight


def compute_recency_decay(collected_at: datetime) -> float:
    """
    Compute recency decay factor using exponential decay.

    Args:
        collected_at: When the item was collected (timezone-aware)

    Returns:
        Decay factor (0.0 to 1.0)

    Formula:
        decay = exp(-λ × hours_since_collected)

    Note:
        - λ = 0.05 gives half-life of ~14 hours
        - After 24h: decay ~0.30 (70% reduction)
        - After 48h: decay ~0.09 (91% reduction)
    """
    now = datetime.now(dt_timezone.utc)

    # Ensure collected_at is timezone-aware
    if collected_at.tzinfo is None:
        # Assume UTC if naive
        collected_at = collected_at.replace(tzinfo=dt_timezone.utc)

    # Calculate hours since collection
    time_delta = now - collected_at
    hours_since = time_delta.total_seconds() / 3600.0

    # Ensure non-negative (in case of clock skew)
    hours_since = max(0.0, hours_since)

    # Exponential decay: exp(-λt)
    decay = math.exp(-RECENCY_LAMBDA * hours_since)

    return decay


def compute_engagement_weight(engagement_signals: dict, rank_position: Optional[int] = None) -> float:
    """
    Compute weighted engagement score from platform signals.

    Args:
        engagement_signals: Dict of engagement metrics from platform
            Expected keys (all optional):
            - upvotes, score, points (Reddit, HN)
            - comments, num_comments, comment_count
            - views, view_count
            - shares, share_count
        rank_position: Position in ranking (1=top, lower is better)

    Returns:
        Weighted engagement score (non-negative float)

    Formula:
        weighted = (upvotes×1.0 + comments×2.0 + views×0.1 +
                   shares×3.0 + (100 - rank)×10)

    Note:
        - Platform-agnostic: extracts common signals from different platforms
        - Handles missing signals gracefully (defaults to 0)
        - Rank position contributes significantly (top 10 items get boost)
    """
    # Extract engagement signals with fallbacks
    upvotes = (
        engagement_signals.get('upvotes', 0) or
        engagement_signals.get('score', 0) or
        engagement_signals.get('points', 0) or
        0
    )

    comments = (
        engagement_signals.get('comments', 0) or
        engagement_signals.get('num_comments', 0) or
        engagement_signals.get('comment_count', 0) or
        0
    )

    views = (
        engagement_signals.get('views', 0) or
        engagement_signals.get('view_count', 0) or
        0
    )

    shares = (
        engagement_signals.get('shares', 0) or
        engagement_signals.get('share_count', 0) or
        0
    )

    # Calculate rank contribution
    # Top positions (1-10) get significant boost
    # Position 100+ gets zero rank boost
    rank_score = 0.0
    if rank_position is not None and rank_position > 0:
        rank_score = max(0.0, 100.0 - rank_position)

    # Weighted sum
    weighted = (
        upvotes * WEIGHT_UPVOTES +
        comments * WEIGHT_COMMENTS +
        views * WEIGHT_VIEWS +
        shares * WEIGHT_SHARES +
        rank_score * WEIGHT_RANK
    )

    # Ensure non-negative
    weighted = max(0.0, weighted)

    logger.debug(
        f"Engagement: upvotes={upvotes}, comments={comments}, "
        f"views={views}, shares={shares}, rank={rank_position} → "
        f"weighted={weighted:.2f}"
    )

    return weighted


def compute_hotness(item) -> float:
    """
    Compute hotness score for a TrendItem.

    Args:
        item: TrendItem instance (must have collected_at, engagement_signals, rank_position)

    Returns:
        Hotness score (typically 0-1000, can be higher for viral content)

    Formula:
        hotness = recency_decay × log10(1 + weighted_engagement) × 100

    Example:
        >>> from crawler_admin.models import TrendItem
        >>> item = TrendItem.objects.get(id=123)
        >>> score = compute_hotness(item)
        >>> item.hotness = score
        >>> item.hotness_computed_at = timezone.now()
        >>> item.save()

    Note:
        - Log scale prevents outliers from dominating
        - Recent items with moderate engagement score higher than
          old items with high engagement
        - Typical new item with 100 upvotes: ~200-300
        - Typical 24h old item with 1000 upvotes: ~100-150
    """
    # Compute recency decay
    recency_decay = compute_recency_decay(item.collected_at)

    # Compute engagement weight
    engagement_weight = compute_engagement_weight(
        item.engagement_signals,
        item.rank_position
    )

    # Log-scale engagement to prevent outliers
    # log10(1 + x) ensures log(1) = 0 for zero engagement
    log_engagement = math.log10(1.0 + engagement_weight)

    # Final hotness score
    hotness = recency_decay * log_engagement * HOTNESS_SCALE

    logger.debug(
        f"Hotness computation: "
        f"recency={recency_decay:.4f}, "
        f"engagement={engagement_weight:.2f}, "
        f"log_engagement={log_engagement:.4f} → "
        f"hotness={hotness:.2f}"
    )

    return hotness


def should_recompute_hotness(item, threshold_hours: float = 6.0, change_threshold_pct: float = 5.0) -> bool:
    """
    Determine if hotness should be recomputed for an item.

    Recompute if:
    1. Never computed (hotness is None)
    2. Computed long ago (>threshold_hours)
    3. Item is recent (<48h) AND last computation was >threshold_hours ago

    Args:
        item: TrendItem instance
        threshold_hours: Recompute if last computation was more than this many hours ago
        change_threshold_pct: Minimum expected change percentage to justify recompute

    Returns:
        True if hotness should be recomputed

    Note:
        - For items <48h old, recompute more frequently (score changes rapidly)
        - For older items, recompute less frequently (score stabilized)
    """
    # Never computed
    if item.hotness is None or item.hotness_computed_at is None:
        return True

    now = datetime.now(dt_timezone.utc)

    # Ensure hotness_computed_at is timezone-aware
    computed_at = item.hotness_computed_at
    if computed_at.tzinfo is None:
        computed_at = computed_at.replace(tzinfo=dt_timezone.utc)

    # Hours since last computation
    hours_since_computed = (now - computed_at).total_seconds() / 3600.0

    # Always recompute if threshold exceeded
    if hours_since_computed >= threshold_hours:
        return True

    # For recent items (<48h old), be more aggressive about recomputing
    collected_at = item.collected_at
    if collected_at.tzinfo is None:
        collected_at = collected_at.replace(tzinfo=dt_timezone.utc)

    hours_since_collected = (now - collected_at).total_seconds() / 3600.0

    if hours_since_collected < 48.0:
        # Recent items: recompute if it's been >threshold_hours/2
        if hours_since_computed >= threshold_hours / 2.0:
            return True

    return False


def estimate_hotness_percentile(hotness: float) -> str:
    """
    Estimate which percentile a hotness score falls into.

    This is a rough guide based on typical distributions.

    Args:
        hotness: Hotness score

    Returns:
        Human-readable percentile estimate (e.g., "Top 1%", "Top 10%")

    Rough distribution:
        - Top 1%: hotness > 500
        - Top 5%: hotness > 300
        - Top 10%: hotness > 200
        - Top 25%: hotness > 100
        - Top 50%: hotness > 50
        - Bottom 50%: hotness < 50
    """
    if hotness >= 500:
        return "Top 1%"
    elif hotness >= 300:
        return "Top 5%"
    elif hotness >= 200:
        return "Top 10%"
    elif hotness >= 100:
        return "Top 25%"
    elif hotness >= 50:
        return "Top 50%"
    else:
        return "Bottom 50%"
