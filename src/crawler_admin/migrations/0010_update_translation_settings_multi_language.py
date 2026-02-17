"""
Migration to enable multi-language translation support.

Changes:
1. Update translation_source_langs: ['en', 'ja'] → [] (empty list = all languages)
2. Update translation_target_locales: ['zh-Hans'] → ['en', 'zh-Hans']

This enables translating top 10% hot posts from ALL languages to both English and Chinese.
Same-language translations (e.g., en→en, zh→zh) are automatically skipped by the selection logic.
"""

from django.db import migrations


def update_translation_settings(apps, schema_editor):
    """Update settings to support all languages → en + zh-Hans translation."""
    SystemSettings = apps.get_model("crawler_admin", "SystemSettings")

    # Update source languages: ['en', 'ja'] → [] (empty list means all languages)
    source_langs_setting = SystemSettings.objects.get(key='translation_source_langs')
    source_langs_setting.value_json = []
    source_langs_setting.description = (
        'Source languages for translation. Set to empty list [] to translate from ALL languages. '
        'Otherwise, specify array like ["en", "ja"] to limit sources.'
    )
    source_langs_setting.updated_by = 'migration_0010'
    source_langs_setting.save()

    # Update target locales: ['zh-Hans'] → ['en', 'zh-Hans']
    target_locales_setting = SystemSettings.objects.get(key='translation_target_locales')
    target_locales_setting.value_json = ['en', 'zh-Hans']
    target_locales_setting.description = (
        'Target locales for translation (JSON array). Items will be translated to each target, '
        'except when source language matches target (auto-skipped).'
    )
    target_locales_setting.updated_by = 'migration_0010'
    target_locales_setting.save()


def reverse_translation_settings(apps, schema_editor):
    """Revert to original settings."""
    SystemSettings = apps.get_model("crawler_admin", "SystemSettings")

    source_langs_setting = SystemSettings.objects.get(key='translation_source_langs')
    source_langs_setting.value_json = ['en', 'ja']
    source_langs_setting.description = 'Source languages eligible for translation (JSON array)'
    source_langs_setting.updated_by = 'migration_rollback'
    source_langs_setting.save()

    target_locales_setting = SystemSettings.objects.get(key='translation_target_locales')
    target_locales_setting.value_json = ['zh-Hans']
    target_locales_setting.description = 'Target locales for translation (JSON array)'
    target_locales_setting.updated_by = 'migration_rollback'
    target_locales_setting.save()


class Migration(migrations.Migration):
    dependencies = [
        ("crawler_admin", "0009_language_aware_selective_translation"),
    ]

    operations = [
        migrations.RunPython(
            update_translation_settings,
            reverse_code=reverse_translation_settings,
        ),
    ]
