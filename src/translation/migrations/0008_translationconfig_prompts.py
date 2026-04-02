# Generated migration: add editable prompt fields to TranslationConfig

from django.db import migrations, models

CANONICAL_PROMPT_DEFAULT = (
    "You are a professional translator producing machine-readable canonical text.\n"
    "Translate the following text from {source_locale} to normalized English (en-US).\n"
    "Source platform: {platform}\n"
    "Produce a stable, literal, machine-consistent English meaning.\n"
    "Return ONLY the translated text, no explanations.\n\n"
    "{text}"
)

DISPLAY_PROMPT_DEFAULT = (
    "You are a professional news translator with expertise in {platform} content.\n"
    "Translate the following text from {source_locale} to {target_locale}.\n"
    "Translate naturally while preserving tone and cultural nuances.\n"
    "Return ONLY the translated text, no explanations.\n\n"
    "{text}"
)

CANONICAL_BATCH_PROMPT_DEFAULT = (
    "You are a professional translator producing machine-readable canonical text.\n"
    "Translate the following from {source_locale} to normalized English (en-US).\n"
    "Source platform: {platform}\n\n"
    "Instructions:\n"
    "- Title: Translate literally and concisely, preserving the key meaning.\n"
    "- Description: Translate the full meaning accurately and consistently.\n\n"
    "Return JSON only, no markdown fences, no explanations:\n"
    '{"title": "translated title here", "description": "translated description here"}\n\n'
    "Title: {title}\n"
    "Description: {description}"
)

DISPLAY_BATCH_PROMPT_DEFAULT = (
    "You are a professional news translator with expertise in {platform} content.\n"
    "Translate the following from {source_locale} to {target_locale}.\n\n"
    "Instructions:\n"
    "- Title: Keep it punchy and engaging, matching the original tone.\n"
    "- Description: Translate naturally, preserving cultural nuances and context.\n\n"
    "Return JSON only, no markdown fences, no explanations:\n"
    '{"title": "translated title here", "description": "translated description here"}\n\n'
    "Title: {title}\n"
    "Description: {description}"
)


class Migration(migrations.Migration):

    dependencies = [
        ("translation", "0007_delete_prompttemplate_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="translationconfig",
            name="canonical_prompt",
            field=models.TextField(
                default=CANONICAL_PROMPT_DEFAULT,
                help_text=(
                    "Prompt for canonical (en-US) single-text translation. "
                    "Variables: {source_locale}, {target_locale}, {platform}, {text}"
                ),
            ),
        ),
        migrations.AddField(
            model_name="translationconfig",
            name="display_prompt",
            field=models.TextField(
                default=DISPLAY_PROMPT_DEFAULT,
                help_text=(
                    "Prompt for display single-text translation. "
                    "Variables: {source_locale}, {target_locale}, {platform}, {text}"
                ),
            ),
        ),
        migrations.AddField(
            model_name="translationconfig",
            name="canonical_batch_prompt",
            field=models.TextField(
                default=CANONICAL_BATCH_PROMPT_DEFAULT,
                help_text=(
                    "Prompt for canonical (en-US) batch translation (title + description). "
                    "Variables: {source_locale}, {target_locale}, {platform}, {title}, {description}"
                ),
            ),
        ),
        migrations.AddField(
            model_name="translationconfig",
            name="display_batch_prompt",
            field=models.TextField(
                default=DISPLAY_BATCH_PROMPT_DEFAULT,
                help_text=(
                    "Prompt for display batch translation (title + description). "
                    "Variables: {source_locale}, {target_locale}, {platform}, {title}, {description}"
                ),
            ),
        ),
    ]
