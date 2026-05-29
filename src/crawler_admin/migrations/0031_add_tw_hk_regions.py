"""
Data migration: add Taiwan (tw) and Hong Kong (hk) region rows.

These two regions were previously missing from the DB, causing chinatimes,
ettoday, mirrormedia, and hk01 surfaces to be registered under `cn` (China)
as a fallback. This migration creates the correct rows so those surfaces can
be re-pointed to the right region.
"""

from django.db import migrations


def add_tw_hk_regions(apps, schema_editor):
    Region = apps.get_model('crawler_admin', 'Region')
    Region.objects.get_or_create(
        key='tw',
        defaults={'name': 'Taiwan', 'default_locale': 'zh-Hant', 'enabled': True},
    )
    Region.objects.get_or_create(
        key='hk',
        defaults={'name': 'Hong Kong', 'default_locale': 'zh-Hant', 'enabled': True},
    )


class Migration(migrations.Migration):

    dependencies = [
        ('crawler_admin', '0030_alter_itemderivation_engine_and_more'),
    ]

    operations = [
        migrations.RunPython(add_tw_hk_regions, migrations.RunPython.noop),
    ]
