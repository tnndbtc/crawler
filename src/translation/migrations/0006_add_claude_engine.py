"""
Replace DeepL/OpenAI with Claude CLI as the sole translation engine.

- Updates ENGINE_CHOICES to claude only
- Updates PROVIDER_CHOICES to claude only
- Migrates existing config to use claude
- Creates ProviderHealth row for claude
- Removes ProviderHealth rows for deepl/openai
"""

from django.db import migrations, models


def migrate_to_claude(apps, schema_editor):
    """Migrate existing data to use Claude engine."""
    TranslationConfig = apps.get_model('translation', 'TranslationConfig')
    ProviderHealth = apps.get_model('translation', 'ProviderHealth')

    # Update existing config to use claude
    for config in TranslationConfig.objects.all():
        config.canonical_engine = 'claude'
        config.display_engine = 'claude'
        config.fallback_order = ['claude']
        config.save()

    # Create claude provider health
    ProviderHealth.objects.get_or_create(
        provider='claude',
        defaults={'state': 'available', 'consecutive_failures': 0},
    )

    # Remove old providers
    ProviderHealth.objects.filter(provider__in=['deepl', 'openai']).delete()


def reverse_migrate(apps, schema_editor):
    """Reverse: restore deepl/openai config."""
    TranslationConfig = apps.get_model('translation', 'TranslationConfig')
    ProviderHealth = apps.get_model('translation', 'ProviderHealth')

    for config in TranslationConfig.objects.all():
        config.canonical_engine = 'deepl'
        config.display_engine = 'deepl'
        config.fallback_order = ['deepl', 'openai']
        config.save()

    for provider in ['deepl', 'openai']:
        ProviderHealth.objects.get_or_create(
            provider=provider,
            defaults={'state': 'available', 'consecutive_failures': 0},
        )

    ProviderHealth.objects.filter(provider='claude').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('translation', '0005_llm_model_prompts'),
    ]

    operations = [
        # Update TranslationConfig engine choices
        migrations.AlterField(
            model_name='translationconfig',
            name='canonical_engine',
            field=models.CharField(
                choices=[('claude', 'Claude CLI')],
                default='claude',
                help_text='Engine for canonical (en-US) translations used in ranking/analysis',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='translationconfig',
            name='display_engine',
            field=models.CharField(
                choices=[('claude', 'Claude CLI')],
                default='claude',
                help_text='Engine for display translations (human-readable UI)',
                max_length=20,
            ),
        ),
        # Update ProviderHealth provider choices
        migrations.AlterField(
            model_name='providerhealth',
            name='provider',
            field=models.CharField(
                choices=[('claude', 'Claude CLI')],
                help_text='Translation provider identifier',
                max_length=20,
                primary_key=True,
                serialize=False,
            ),
        ),
        # Migrate data
        migrations.RunPython(migrate_to_claude, reverse_migrate),
    ]
