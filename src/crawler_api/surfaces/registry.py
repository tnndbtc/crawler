"""
Collector registry and dynamic loading utilities.

Loads collectors from entrypoint strings like:
- "crawler_api.surfaces.reddit_hot:collect"
- "crawler_api.surfaces.youtube_trending:collect"

From REQUIREMENTS-MASTER.md:
TrendSurface.entrypoint contains Python import path to collector function.
"""

import importlib
import logging
from typing import Optional

from .collector_interface import SurfaceCollector

logger = logging.getLogger(__name__)


class CollectorRegistry:
    """
    Registry for loading and caching collectors.

    Collectors are loaded dynamically from entrypoint strings.
    Caching prevents repeated imports.
    """

    def __init__(self):
        self._cache: dict[str, SurfaceCollector] = {}

    def get_collector(self, entrypoint: str) -> SurfaceCollector:
        """
        Load collector from entrypoint string.

        Args:
            entrypoint: Python import path, e.g. "crawler_api.surfaces.reddit_hot:collect"

        Returns:
            Loaded collector function

        Raises:
            ImportError: If module or function not found
            ValueError: If entrypoint format is invalid

        Example:
            >>> registry = CollectorRegistry()
            >>> collector = registry.get_collector("crawler_api.surfaces.reddit_hot:collect")
            >>> items, cursor = await collector(config={}, cursor=None, limit=100)
        """
        # Check cache
        if entrypoint in self._cache:
            logger.debug(f"Collector cache hit: {entrypoint}")
            return self._cache[entrypoint]

        # Parse entrypoint
        if ':' not in entrypoint:
            raise ValueError(
                f"Invalid entrypoint format: '{entrypoint}'. "
                f"Expected 'module.path:function_name'"
            )

        module_path, function_name = entrypoint.split(':', 1)

        # Import module
        try:
            module = importlib.import_module(module_path)
        except ImportError as e:
            raise ImportError(
                f"Failed to import module '{module_path}' from entrypoint '{entrypoint}': {e}"
            ) from e

        # Get function
        if not hasattr(module, function_name):
            raise ImportError(
                f"Module '{module_path}' has no function '{function_name}'. "
                f"Entrypoint: '{entrypoint}'"
            )

        collector = getattr(module, function_name)

        # Verify it's callable
        if not callable(collector):
            raise ValueError(
                f"Entrypoint '{entrypoint}' resolved to {type(collector).__name__}, "
                f"but collectors must be callable functions"
            )

        # Cache and return
        self._cache[entrypoint] = collector
        logger.info(f"Loaded collector: {entrypoint}")
        return collector

    def clear_cache(self):
        """Clear the collector cache (useful for testing)."""
        self._cache.clear()
        logger.info("Collector cache cleared")


# Global registry instance
_registry = CollectorRegistry()


def get_collector(entrypoint: str) -> SurfaceCollector:
    """
    Get collector from global registry.

    Convenience function for common use case.

    Args:
        entrypoint: Python import path, e.g. "crawler_api.surfaces.reddit_hot:collect"

    Returns:
        Loaded collector function

    Example:
        >>> from crawler_api.surfaces.registry import get_collector
        >>> collector = get_collector("crawler_api.surfaces.reddit_hot:collect")
        >>> items, cursor = await collector(config={}, cursor=None, limit=100)
    """
    return _registry.get_collector(entrypoint)


def clear_collector_cache():
    """Clear the global collector cache."""
    _registry.clear_cache()
