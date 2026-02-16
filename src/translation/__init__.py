"""
Translation Engine - Pluggable multi-engine translation system.

This Django app provides:
- Configurable translation engines (DeepL, OpenAI)
- Admin-managed prompt templates for LLM-based translations
- Fallback handling with configurable engine priority
- Separate canonical (en-US) and display translation pipelines

Architecture:
- BaseTranslationEngine: Abstract base for all engines
- TranslationManager: Orchestrator with caching and fallback
- TranslationConfig: Database-backed configuration
- PromptTemplate: Admin-editable prompts for LLM engines
"""

from .base import BaseTranslationEngine, TranslationResult
from .manager import TranslationManager

__all__ = [
    'BaseTranslationEngine',
    'TranslationResult',
    'TranslationManager',
]
