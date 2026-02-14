"""
Django app configuration for crawler_admin.
"""

from django.apps import AppConfig


class CrawlerAdminConfig(AppConfig):
    """Configuration for the crawler_admin Django app."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'crawler_admin'
    verbose_name = 'Trend Crawler Administration'
