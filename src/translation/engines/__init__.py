"""
Translation engine registry.

All engines must be registered here to be available for use.
"""

from typing import Dict, Type, TYPE_CHECKING

if TYPE_CHECKING:
    from translation.base import BaseTranslationEngine

# Engine registry: name -> engine class
_ENGINE_REGISTRY: Dict[str, Type['BaseTranslationEngine']] = {}


def register_engine(name: str):
    """
    Decorator to register a translation engine.

    Usage:
        @register_engine('deepl')
        class DeepLEngine(BaseTranslationEngine):
            ...
    """
    def decorator(cls: Type['BaseTranslationEngine']):
        _ENGINE_REGISTRY[name] = cls
        return cls
    return decorator


def get_engine_class(name: str) -> Type['BaseTranslationEngine']:
    """
    Get engine class by name.

    Args:
        name: Engine name (e.g., 'deepl', 'openai', 'local')

    Returns:
        Engine class

    Raises:
        ValueError: If engine not found
    """
    if name not in _ENGINE_REGISTRY:
        available = ', '.join(_ENGINE_REGISTRY.keys())
        raise ValueError(f"Unknown engine: {name}. Available: {available}")
    return _ENGINE_REGISTRY[name]


def get_available_engines() -> Dict[str, Type['BaseTranslationEngine']]:
    """Get all registered engines."""
    return _ENGINE_REGISTRY.copy()


# Import engines to register them
from .deepl_engine import DeepLEngine  # noqa: F401, E402
from .openai_engine import OpenAIEngine  # noqa: F401, E402
# LocalEngine (argostranslate) removed due to poor quality

# Conditionally import test engine if TRANSLATION_TEST_MODE=true
import os
if os.getenv('TRANSLATION_TEST_MODE', 'false').lower() == 'true':
    from .test_engine import DeterministicTestEngine  # noqa: F401, E402
    import logging
    logger = logging.getLogger(__name__)
    logger.warning("Test translator enabled (TRANSLATION_TEST_MODE=true) - using deterministic fallback")
