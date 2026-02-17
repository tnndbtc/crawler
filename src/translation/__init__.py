"""
Translation Engine - Pluggable multi-engine translation system.

This Django app provides:
- Configurable translation engines (DeepL, OpenAI)
- Admin-managed prompts for LLM-based translations (in TranslationConfig)
- Fallback handling with configurable engine priority
- Separate canonical (en-US) and display translation pipelines

Architecture:
- BaseTranslationEngine: Abstract base for all engines
- TranslationManager: Orchestrator with caching and fallback
- TranslationConfig: Database-backed configuration with embedded prompts
"""

from .base import BaseTranslationEngine, TranslationResult
from .manager import TranslationManager

__all__ = [
    'BaseTranslationEngine',
    'TranslationResult',
    'TranslationManager',
]
