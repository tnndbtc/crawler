"""
Helper for reading the top_comments setting used by surface collectors.

Priority order:
  1. config['top_comments'] — per-surface override (set in TrendSurface.config_json)
  2. SystemSettings['crawler.top_comments_per_post'] — global Django admin setting
  3. Built-in default (5)

Set the value to 0 to disable comment fetching entirely for a surface.
"""

import logging

logger = logging.getLogger(__name__)

# Key used in SystemSettings table
SETTING_KEY = 'crawler.top_comments_per_post'

# Hardcoded fallback used only when Django is unavailable
_BUILTIN_DEFAULT = 5

try:
    from asgiref.sync import sync_to_async
    _DJANGO_AVAILABLE = True
except ImportError:
    _DJANGO_AVAILABLE = False


async def get_top_comments_count(config: dict) -> int:
    """
    Return the number of top comments to fetch per post for a surface collector.

    Args:
        config: TrendSurface.config_json dict for the current surface run.

    Returns:
        Number of comments to fetch (0 = disabled).
    """
    # 1. Per-surface config_json override is highest priority
    if 'top_comments' in config:
        try:
            return max(0, int(config['top_comments']))
        except (TypeError, ValueError):
            logger.warning(
                f"Invalid top_comments value in surface config: {config['top_comments']!r}. "
                "Falling back to global setting."
            )

    # 2. Global SystemSettings entry (editable from Django admin)
    if _DJANGO_AVAILABLE:
        try:
            from crawler_admin.models import SystemSettings
            value = await sync_to_async(SystemSettings.get_setting)(
                SETTING_KEY, default=_BUILTIN_DEFAULT
            )
            return max(0, int(value))
        except Exception as e:
            logger.debug(
                f"Could not read SystemSettings[{SETTING_KEY!r}]: {e}. "
                "Using built-in default."
            )

    # 3. Built-in fallback
    return _BUILTIN_DEFAULT
