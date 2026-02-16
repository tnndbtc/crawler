"""
Base classes for translation engines.

Provides:
- TranslationResult: Standardized result container
- BaseTranslationEngine: Abstract base class for all engines
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Dict, Any


@dataclass
class TranslationResult:
    """
    Result container for translation operations.

    Attributes:
        title: Translated title
        description: Translated description (optional)
        engine: Name of engine that produced the result
        source_locale: Source language locale
        target_locale: Target language locale
        error: Error message if translation failed
        metadata: Additional metadata from the engine
    """
    title: str
    description: Optional[str] = None
    engine: str = 'unknown'
    source_locale: str = ''
    target_locale: str = ''
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        """Check if translation was successful."""
        return self.error is None

    @classmethod
    def from_error(
        cls,
        error: str,
        engine: str,
        source_locale: str = '',
        target_locale: str = ''
    ) -> 'TranslationResult':
        """Create a failed result from an error message."""
        return cls(
            title='',
            description=None,
            engine=engine,
            source_locale=source_locale,
            target_locale=target_locale,
            error=error
        )

    @classmethod
    def no_translation_needed(
        cls,
        title: str,
        description: Optional[str],
        source_locale: str,
        target_locale: str
    ) -> 'TranslationResult':
        """Create a result when no translation is needed (same language)."""
        return cls(
            title=title,
            description=description,
            engine='none',
            source_locale=source_locale,
            target_locale=target_locale,
            metadata={'reason': 'same_language'}
        )


class BaseTranslationEngine(ABC):
    """
    Abstract base class for translation engines.

    All translation engines must implement this interface.
    Engines should be stateless and thread-safe.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """
        Unique name for this engine.

        Used for logging, configuration, and result attribution.
        """
        ...

    @abstractmethod
    async def translate(
        self,
        text: str,
        source_locale: str,
        target_locale: str,
        translation_type: str = 'display',
        context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Translate a single text string.

        Args:
            text: Text to translate
            source_locale: Source locale code (e.g., "ja-JP")
            target_locale: Target locale code (e.g., "en-US")
            translation_type: 'canonical' or 'display'
            context: Additional context for translation
                     (source_platform, region, title, description, etc.)

        Returns:
            Translated text

        Raises:
            TranslationError: If translation fails
        """
        ...

    @abstractmethod
    def supports_locale_pair(self, source: str, target: str) -> bool:
        """
        Check if this engine supports the given locale pair.

        Args:
            source: Source locale code
            target: Target locale code

        Returns:
            True if this engine can translate between these locales
        """
        ...

    async def translate_item(
        self,
        title: str,
        description: Optional[str],
        source_locale: str,
        target_locale: str,
        translation_type: str = 'display',
        context: Optional[Dict[str, Any]] = None
    ) -> TranslationResult:
        """
        Translate a complete item (title + description).

        Default implementation calls translate() twice.
        Engines may override for batch optimization.

        Args:
            title: Original title
            description: Original description (optional)
            source_locale: Source locale code
            target_locale: Target locale code
            translation_type: 'canonical' or 'display'
            context: Additional context

        Returns:
            TranslationResult with translated content
        """
        try:
            # Translate title
            translated_title = await self.translate(
                text=title,
                source_locale=source_locale,
                target_locale=target_locale,
                translation_type=translation_type,
                context=context
            )

            # Translate description if present
            translated_description = None
            if description:
                translated_description = await self.translate(
                    text=description,
                    source_locale=source_locale,
                    target_locale=target_locale,
                    translation_type=translation_type,
                    context=context
                )

            return TranslationResult(
                title=translated_title,
                description=translated_description,
                engine=self.name,
                source_locale=source_locale,
                target_locale=target_locale
            )

        except Exception as e:
            return TranslationResult.from_error(
                error=str(e),
                engine=self.name,
                source_locale=source_locale,
                target_locale=target_locale
            )

    async def close(self):
        """
        Clean up engine resources.

        Override if the engine holds resources that need cleanup.
        """
        pass

    def __str__(self) -> str:
        return f"<{self.__class__.__name__}: {self.name}>"

    def __repr__(self) -> str:
        return self.__str__()


class TranslationError(Exception):
    """Base exception for translation errors."""

    def __init__(self, message: str, engine: str = 'unknown', recoverable: bool = True):
        super().__init__(message)
        self.engine = engine
        self.recoverable = recoverable


class QuotaExceededError(TranslationError):
    """Raised when API quota is exceeded."""

    def __init__(self, message: str, engine: str = 'unknown'):
        super().__init__(message, engine=engine, recoverable=False)


class AuthenticationError(TranslationError):
    """Raised when API authentication fails."""

    def __init__(self, message: str, engine: str = 'unknown'):
        super().__init__(message, engine=engine, recoverable=False)


class RateLimitError(TranslationError):
    """Raised when rate limit is hit (temporary)."""

    def __init__(self, message: str, engine: str = 'unknown', retry_after: Optional[int] = None):
        super().__init__(message, engine=engine, recoverable=True)
        self.retry_after = retry_after
