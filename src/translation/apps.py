"""Django app configuration for translation engine."""

from django.apps import AppConfig


class TranslationAppConfig(AppConfig):
    """Translation engine app configuration."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'translation'
    verbose_name = 'Translation Engine'

    def ready(self):
        """Initialize translation engine on app startup."""
        # Import engines to register them
        from . import engines  # noqa: F401
