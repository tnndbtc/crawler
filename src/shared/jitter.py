"""
Jitter utilities for avoiding thundering herd problems.

Provides randomization functions to prevent synchronized polling/requests
across multiple worker instances.

Usage:
    import asyncio
    from shared.jitter import jitter, jittered_sleep

    # Add jitter to a fixed interval
    await asyncio.sleep(60 + jitter(60))  # 60s ± 30%

    # Convenience wrapper
    await jittered_sleep(60)  # 60s ± 30%

    # Custom jitter percentage
    await asyncio.sleep(60 + jitter(60, percent=0.5))  # 60s ± 50%
"""

import random
import asyncio
from typing import Union


def jitter(base: Union[int, float], percent: float = 0.3) -> float:
    """
    Add random jitter to a base value.

    Args:
        base: Base value (seconds, typically)
        percent: Jitter percentage (default: 0.3 = ±30%)

    Returns:
        Random offset in range [-base*percent, +base*percent]

    Examples:
        >>> jitter(60)  # Returns value in range [-18, +18]
        >>> jitter(60, percent=0.5)  # Returns value in range [-30, +30]
    """
    if base <= 0:
        return 0.0

    max_jitter = base * percent
    return random.uniform(-max_jitter, max_jitter)


async def jittered_sleep(seconds: Union[int, float], percent: float = 0.3):
    """
    Sleep for a duration with random jitter.

    Args:
        seconds: Base sleep duration
        percent: Jitter percentage (default: 0.3 = ±30%)

    Examples:
        >>> await jittered_sleep(60)  # Sleep ~42-78 seconds
        >>> await jittered_sleep(60, percent=0.1)  # Sleep ~54-66 seconds
    """
    jittered_duration = max(0, seconds + jitter(seconds, percent))
    await asyncio.sleep(jittered_duration)


def exponential_backoff(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter_percent: float = 0.3
) -> float:
    """
    Calculate exponential backoff delay with jitter.

    Args:
        attempt: Attempt number (0-indexed, so 0 = first retry)
        base_delay: Base delay in seconds (default: 1.0)
        max_delay: Maximum delay cap (default: 60.0)
        jitter_percent: Jitter percentage (default: 0.3 = ±30%)

    Returns:
        Delay in seconds with exponential backoff and jitter

    Examples:
        >>> exponential_backoff(0)  # ~0.7-1.3s (1s ± 30%)
        >>> exponential_backoff(1)  # ~1.4-2.6s (2s ± 30%)
        >>> exponential_backoff(2)  # ~2.8-5.2s (4s ± 30%)
        >>> exponential_backoff(10)  # Capped at max_delay
    """
    # Calculate 2^attempt * base_delay, capped at max_delay
    delay = min(base_delay * (2 ** attempt), max_delay)

    # Add jitter
    jittered_delay = max(0, delay + jitter(delay, jitter_percent))

    return jittered_delay


async def exponential_backoff_sleep(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter_percent: float = 0.3
):
    """
    Sleep with exponential backoff and jitter.

    Args:
        attempt: Attempt number (0-indexed)
        base_delay: Base delay in seconds (default: 1.0)
        max_delay: Maximum delay cap (default: 60.0)
        jitter_percent: Jitter percentage (default: 0.3 = ±30%)

    Examples:
        >>> for attempt in range(5):
        ...     await exponential_backoff_sleep(attempt)
        ...     # Try operation
    """
    delay = exponential_backoff(attempt, base_delay, max_delay, jitter_percent)
    await asyncio.sleep(delay)
