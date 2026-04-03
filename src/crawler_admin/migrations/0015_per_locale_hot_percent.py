# Migration: add per-locale translation hot percentage settings to SystemSettings
# Each locale gets its own key (translation_hot_percent_{lang}) that overrides
# the global translation_hot_percent setting.
# Values: en=5 (English is already the source for most content),
#         all others=2 (conservative default for new locales).

from django.db import migrations


LOCALE_SETTINGS = [
    ('zh', 'Chinese (Simplified)'),
    ('ja', 'Japanese'),
    ('ko', 'Korean'),
    ('en', 'English'),
    ('es', 'Spanish'),
    ('pt', 'Portuguese (Brazil)'),
    ('fr', 'French'),
    ('de', 'German'),
    ('ar', 'Arabic'),
    ('hi', 'Hindi'),
    ('id', 'Indonesian'),
    ('ru', 'Russian'),
    ('tr', 'Turkish'),
    ('it', 'Italian'),
    ('pl', 'Polish'),
]


def add_per_locale_settings(apps, schema_editor):
    SystemSettings = apps.get_model('crawler_admin', 'SystemSettings')
    for lang, lang_name in LOCALE_SETTINGS:
        key = f'translation_hot_percent_{lang}'
        value = 5 if lang == 'en' else 2
        SystemSettings.objects.get_or_create(
            key=key,
            defaults={
                'value_json': value,
                'description': (
                    f'Translation hot % for {lang_name} ({lang}) target locale. '
                    f'Overrides global translation_hot_percent. '
                    f'en=5 (already primary source language), all others=2.'
                ),
                'value_type': 'int',
            }
        )


def remove_per_locale_settings(apps, schema_editor):
    SystemSettings = apps.get_model('crawler_admin', 'SystemSettings')
    for lang, _ in LOCALE_SETTINGS:
        SystemSettings.objects.filter(key=f'translation_hot_percent_{lang}').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('crawler_admin', '0014_trenditem_summary_status'),
    ]

    operations = [
        migrations.RunPython(add_per_locale_settings, remove_per_locale_settings),
    ]
