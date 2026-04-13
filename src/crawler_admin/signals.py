"""
Audit signals — log any attempt to delete critical configuration data.

Monitors:
  - Region, TrendSurface, SystemSettings deletions (pre_delete signal)
  - manage.py flush / migrate operations (pre_migrate signal)

All events are written to logs/audit.log with a full stack trace so the
exact caller (code path, script, or management command) can be identified.
"""

import logging
import traceback

from django.db.models.signals import pre_delete
from django.dispatch import receiver

logger = logging.getLogger('audit')


def _log_deletion(model_name, instance_repr, extra=''):
    """Log a deletion event with full stack trace."""
    stack = ''.join(traceback.format_stack())
    logger.warning(
        f"\n"
        f"{'='*60}\n"
        f"⚠️  DELETION DETECTED: {model_name}\n"
        f"  Instance : {instance_repr}\n"
        f"  {extra}"
        f"Stack trace (most recent call last):\n{stack}"
        f"{'='*60}"
    )


def connect_signals():
    """
    Connect all audit signals.
    Called from CrawlerAdminConfig.ready().
    """
    from crawler_admin.models import Region, TrendSurface, SystemSettings
    from django.contrib.auth.models import User

    # -------------------------------------------------------------------------
    # Region deletion
    # -------------------------------------------------------------------------
    @receiver(pre_delete, sender=Region, weak=False)
    def on_region_delete(sender, instance, **kwargs):
        _log_deletion(
            model_name='Region',
            instance_repr=f"key={instance.key!r} name={instance.name!r}",
        )

    # -------------------------------------------------------------------------
    # TrendSurface deletion
    # -------------------------------------------------------------------------
    @receiver(pre_delete, sender=TrendSurface, weak=False)
    def on_surface_delete(sender, instance, **kwargs):
        _log_deletion(
            model_name='TrendSurface',
            instance_repr=f"key={instance.key!r} platform={instance.platform!r}",
        )

    # -------------------------------------------------------------------------
    # SystemSettings deletion
    # -------------------------------------------------------------------------
    @receiver(pre_delete, sender=SystemSettings, weak=False)
    def on_settings_delete(sender, instance, **kwargs):
        _log_deletion(
            model_name='SystemSettings',
            instance_repr=f"key={instance.key!r} value={instance.value_json!r}",
        )

    # -------------------------------------------------------------------------
    # User deletion
    # -------------------------------------------------------------------------
    @receiver(pre_delete, sender=User, weak=False)
    def on_user_delete(sender, instance, **kwargs):
        _log_deletion(
            model_name='User',
            instance_repr=f"username={instance.username!r} is_superuser={instance.is_superuser}",
        )

    # -------------------------------------------------------------------------
    # manage.py flush / migrate — fires pre_migrate before wiping tables
    # -------------------------------------------------------------------------
    from django.db.models.signals import pre_migrate

    def on_pre_migrate(sender, app_config, verbosity, interactive, using, **kwargs):
        import sys
        if 'flush' not in sys.argv:
            return
        stack = ''.join(traceback.format_stack())
        logger.warning(
            f"\n"
            f"{'='*60}\n"
            f"⚠️  FLUSH DETECTED\n"
            f"  App      : {app_config.name}\n"
            f"  DB alias : {using}\n"
            f"Stack trace:\n{stack}"
            f"{'='*60}"
        )

    pre_migrate.connect(on_pre_migrate, weak=False)

    logger.info("Audit signals connected (Region, TrendSurface, SystemSettings, User, pre_migrate)")
