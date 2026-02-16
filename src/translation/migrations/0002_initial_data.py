"""
Data migration to create initial translation configuration.

Creates:
- Default TranslationConfig (singleton)
- Default LLMModelConfig (gpt-4o-mini)
- Default PromptTemplates (canonical and display)
"""

from django.db import migrations


def create_initial_data(apps, schema_editor):
    """Create initial translation configuration data."""
    TranslationConfig = apps.get_model('translation', 'TranslationConfig')
    LLMModelConfig = apps.get_model('translation', 'LLMModelConfig')
    PromptTemplate = apps.get_model('translation', 'PromptTemplate')

    # Create TranslationConfig singleton
    TranslationConfig.objects.get_or_create(
        pk=1,
        defaults={
            'canonical_engine': 'deepl',
            'display_engine': 'deepl',
            'fallback_order': ['deepl', 'openai', 'local'],
            'enable_translation': True,
            'production_mode': True,
            'canonical_locale': 'en-US',
            'enabled_locales': ['zh-Hans'],
            'max_chars_per_request': 5000,
            'batch_size': 10,
        }
    )

    # Create default LLM model config
    LLMModelConfig.objects.get_or_create(
        name='GPT-4o Mini',
        defaults={
            'provider': 'openai',
            'model_id': 'gpt-4o-mini',
            'temperature': 0.3,
            'max_tokens': 1000,
            'enabled': True,
            'is_default': True,
            'description': 'Cost-effective model for translation tasks. Good balance of quality and speed.',
        }
    )

    # Create canonical prompt template
    PromptTemplate.objects.get_or_create(
        name='Default Canonical',
        defaults={
            'translation_type': 'canonical',
            'system_prompt': (
                "You are a semantic normalization translator. "
                "Translate to English (en-US) preserving exact meaning. "
                "Normalize formatting for text similarity comparison. "
                "Output only the translated text, no explanations."
            ),
            'developer_prompt': '',
            'user_prompt_template': (
                "Translate this {original_language} text from {source_platform} ({region}) "
                "to normalized English:\n\n"
                "Title: {title}\n"
                "Description: {description}"
            ),
            'description': 'Default template for canonical translations (semantic normalization for analysis)',
            'active': True,
            'is_default': True,
        }
    )

    # Create display prompt template
    PromptTemplate.objects.get_or_create(
        name='Default Display',
        defaults={
            'translation_type': 'display',
            'system_prompt': (
                "You are a professional translator. "
                "Translate naturally while preserving tone and cultural nuances. "
                "Output only the translated text, no explanations."
            ),
            'developer_prompt': '',
            'user_prompt_template': (
                "Translate this {original_language} content to {target_locale}:\n\n"
                "Title: {title}\n"
                "Description: {description}"
            ),
            'description': 'Default template for display translations (human-readable UI)',
            'active': True,
            'is_default': True,
        }
    )


def reverse_initial_data(apps, schema_editor):
    """Remove initial translation configuration data."""
    TranslationConfig = apps.get_model('translation', 'TranslationConfig')
    LLMModelConfig = apps.get_model('translation', 'LLMModelConfig')
    PromptTemplate = apps.get_model('translation', 'PromptTemplate')

    TranslationConfig.objects.filter(pk=1).delete()
    LLMModelConfig.objects.filter(name='GPT-4o Mini').delete()
    PromptTemplate.objects.filter(name__startswith='Default').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('translation', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_initial_data, reverse_initial_data),
    ]
