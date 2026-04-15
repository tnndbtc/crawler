"""
Data migration: seed the crawler.top_comments_per_post SystemSettings entry.

This setting controls how many top comments each surface collector fetches
per post / video. It is readable and editable from the Django admin under
System Settings.

Default value: 5

Per-surface override: set config_json.top_comments on a TrendSurface to
override for that surface only (0 = disable comment fetching for that surface).
"""

from django.db import migrations


def seed_forward(apps, schema_editor):
    SystemSettings = apps.get_model('crawler_admin', 'SystemSettings')
    SystemSettings.objects.update_or_create(
        key='crawler.top_comments_per_post',
        defaults={
            'value_json': 5,
            'value_type': 'integer',
            'description': (
                'Number of top comments to fetch per post or video for surfaces that '
                'support it (Reddit, YouTube, Hacker News, Bilibili). '
                'Set to 0 to disable comment fetching globally. '
                'Can be overridden per-surface via TrendSurface.config_json["top_comments"].'
            ),
            'updated_by': 'migration',
        },
    )


def seed_reverse(apps, schema_editor):
    SystemSettings = apps.get_model('crawler_admin', 'SystemSettings')
    SystemSettings.objects.filter(key='crawler.top_comments_per_post').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('crawler_admin', '0027_backfill_classification_state'),
    ]

    operations = [
        migrations.RunPython(seed_forward, seed_reverse),
    ]
