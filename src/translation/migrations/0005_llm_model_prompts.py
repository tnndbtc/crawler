"""
Migration to move prompts into LLMModelConfig.

Changes:
- Adds prompt fields (system_prompt, developer_prompt, user_prompt) to LLMModelConfig
- Adds canonical_model and display_model ForeignKeys to TranslationConfig
- Creates two default model entries with prompts
- Links TranslationConfig to these models
"""

from django.db import migrations, models
import django.db.models.deletion


# Default prompts
CANONICAL_SYSTEM = (
    "You are a semantic normalization engine. Your task is NOT to produce natural English. "
    "Produce stable, literal, machine-consistent English meaning. Remove jokes, sarcasm, slang, exaggeration. "
    "Rewrite ambiguity into explicit meaning. Prefer neutral factual wording. Do not add commentary. "
    "Return only the normalized sentence(s)."
)
CANONICAL_DEVELOPER = (
    "Normalize the following content into canonical English meaning for clustering and ranking. "
    "Keep topic/subject/object/intent. Remove clickbait, humor, emotional tone. "
    "Output must be short and consistent."
)
CANONICAL_USER = (
    "Source: {source_platform}\n"
    "Locale: {original_language}\n"
    "Title: {title}\n"
    "Description: {description}"
)

DISPLAY_SYSTEM = (
    "You are a professional multilingual news translator. "
    "Translate naturally for native readers. Keep original meaning and tone. "
    "Preserve proper nouns. Do not summarize. Do not explain. Return only the translation."
)
DISPLAY_DEVELOPER = (
    "Translate into {target_locale} for user display. Natural wording. Preserve tone. "
    "Keep names/brands/places accurate. Avoid overly literal phrasing."
)
DISPLAY_USER = (
    "Source: {source_platform}\n"
    "From: {original_language}\n"
    "To: {target_locale}\n"
    "Title: {title}\n"
    "Description: {description}"
)


def create_model_entries(apps, schema_editor):
    """Create default LLM model entries with prompts and link to TranslationConfig."""
    LLMModelConfig = apps.get_model('translation', 'LLMModelConfig')
    TranslationConfig = apps.get_model('translation', 'TranslationConfig')

    # Check if we already have entries (e.g., GPT-4o Mini)
    existing_model = LLMModelConfig.objects.filter(provider='openai').first()
    base_model_id = existing_model.model_id if existing_model else 'gpt-4o-mini'
    base_temp = existing_model.temperature if existing_model else 0.3
    base_top_p = getattr(existing_model, 'top_p', 1.0) if existing_model else 1.0
    base_max_tokens = existing_model.max_tokens if existing_model else 1000

    # Create canonical model entry
    canonical_model, _ = LLMModelConfig.objects.get_or_create(
        name='GPT-4o-mini Canonical',
        defaults={
            'provider': 'openai',
            'model_id': base_model_id,
            'temperature': base_temp,
            'top_p': base_top_p,
            'max_tokens': base_max_tokens,
            'system_prompt': CANONICAL_SYSTEM,
            'developer_prompt': CANONICAL_DEVELOPER,
            'user_prompt': CANONICAL_USER,
            'enabled': True,
            'is_default': False,
        }
    )

    # Create display model entry
    display_model, _ = LLMModelConfig.objects.get_or_create(
        name='GPT-4o-mini Display',
        defaults={
            'provider': 'openai',
            'model_id': base_model_id,
            'temperature': base_temp,
            'top_p': base_top_p,
            'max_tokens': base_max_tokens,
            'system_prompt': DISPLAY_SYSTEM,
            'developer_prompt': DISPLAY_DEVELOPER,
            'user_prompt': DISPLAY_USER,
            'enabled': True,
            'is_default': False,
        }
    )

    # Link TranslationConfig to these models
    config, _ = TranslationConfig.objects.get_or_create(pk=1)
    config.canonical_model = canonical_model
    config.display_model = display_model
    config.save()


def reverse_model_entries(apps, schema_editor):
    """Remove the created model entries."""
    LLMModelConfig = apps.get_model('translation', 'LLMModelConfig')
    LLMModelConfig.objects.filter(name__in=['GPT-4o-mini Canonical', 'GPT-4o-mini Display']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('translation', '0004_providerhealth'),
    ]

    operations = [
        # Step 1: Add prompt fields to LLMModelConfig
        migrations.AddField(
            model_name='llmmodelconfig',
            name='system_prompt',
            field=models.TextField(blank=True, default='', help_text='System prompt (defines AI behavior)'),
        ),
        migrations.AddField(
            model_name='llmmodelconfig',
            name='developer_prompt',
            field=models.TextField(blank=True, default='', help_text='Developer prompt (additional instructions)'),
        ),
        migrations.AddField(
            model_name='llmmodelconfig',
            name='user_prompt',
            field=models.TextField(blank=True, default='', help_text='User prompt template. Variables: {source_platform}, {original_language}, {target_locale}, {title}, {description}'),
        ),

        # Step 2: Increase name field length
        migrations.AlterField(
            model_name='llmmodelconfig',
            name='name',
            field=models.CharField(help_text="Human-readable name (e.g., 'GPT-4o-mini Canonical', 'GPT-4o-mini Display')", max_length=100, unique=True),
        ),

        # Step 3: Add canonical_model and display_model ForeignKeys to TranslationConfig
        migrations.AddField(
            model_name='translationconfig',
            name='canonical_model',
            field=models.ForeignKey(blank=True, help_text='LLM model to use for canonical translations (when OpenAI is selected)', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='canonical_configs', to='translation.llmmodelconfig'),
        ),
        migrations.AddField(
            model_name='translationconfig',
            name='display_model',
            field=models.ForeignKey(blank=True, help_text='LLM model to use for display translations (when OpenAI is selected)', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='display_configs', to='translation.llmmodelconfig'),
        ),

        # Step 4: Create default model entries and link them
        migrations.RunPython(create_model_entries, reverse_model_entries),
    ]
