# Generated migration for Feature A: Chinese Translation Layer
from django.db import migrations


def enable_zh_hans_locale(apps, schema_editor):
    """
    Enable zh-Hans (Simplified Chinese) as an additional translation locale.

    This allows the translation worker to create Chinese translations
    for English content (en-US → zh-Hans) in addition to the existing
    canonical English translations (non-English → en-US).
    """
    TranslationSettings = apps.get_model('crawler_admin', 'TranslationSettings')

    # Get or create the singleton settings instance
    settings, created = TranslationSettings.objects.get_or_create(pk=1)

    # Add zh-Hans to enabled_locales if not already present
    if 'zh-Hans' not in settings.enabled_locales:
        settings.enabled_locales.append('zh-Hans')
        settings.save()
        print(f"✓ Added zh-Hans to enabled_locales: {settings.enabled_locales}")
    else:
        print(f"ℹ zh-Hans already in enabled_locales: {settings.enabled_locales}")


def disable_zh_hans_locale(apps, schema_editor):
    """Reverse migration: remove zh-Hans from enabled_locales."""
    TranslationSettings = apps.get_model('crawler_admin', 'TranslationSettings')

    try:
        settings = TranslationSettings.objects.get(pk=1)
        if 'zh-Hans' in settings.enabled_locales:
            settings.enabled_locales.remove('zh-Hans')
            settings.save()
            print(f"✓ Removed zh-Hans from enabled_locales: {settings.enabled_locales}")
    except TranslationSettings.DoesNotExist:
        print("ℹ TranslationSettings not found, nothing to reverse")


class Migration(migrations.Migration):

    dependencies = [
        ('crawler_admin', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(enable_zh_hans_locale, reverse_code=disable_zh_hans_locale),
    ]
