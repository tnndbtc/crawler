# Generated manually - Language-aware selective translation system
# Adds language detection fields, hotness scoring, ItemDerivation table, and SystemSettings

from django.db import migrations, models
import django.db.models.deletion


def create_initial_system_settings(apps, schema_editor):
    """Create initial system settings for selective translation."""
    SystemSettings = apps.get_model("crawler_admin", "SystemSettings")

    # Translation configuration
    SystemSettings.objects.get_or_create(
        key='translation_hot_percent',
        defaults={
            'value_json': 10,
            'description': 'Top X% hottest items to translate per language group',
            'value_type': 'integer',
            'updated_by': 'migration',
        }
    )

    SystemSettings.objects.get_or_create(
        key='translation_small_bucket_min',
        defaults={
            'value_json': 1,
            'description': 'Minimum items to translate for small buckets (<20 items)',
            'value_type': 'integer',
            'updated_by': 'migration',
        }
    )

    SystemSettings.objects.get_or_create(
        key='translation_small_bucket_max',
        defaults={
            'value_json': 5,
            'description': 'Maximum items to translate for small buckets (<20 items)',
            'value_type': 'integer',
            'updated_by': 'migration',
        }
    )

    SystemSettings.objects.get_or_create(
        key='translation_target_locales',
        defaults={
            'value_json': ['zh-Hans'],
            'description': 'Target locales for translation (JSON array)',
            'value_type': 'json',
            'updated_by': 'migration',
        }
    )

    SystemSettings.objects.get_or_create(
        key='translation_source_langs',
        defaults={
            'value_json': ['en', 'ja'],
            'description': 'Source languages eligible for translation (JSON array)',
            'value_type': 'json',
            'updated_by': 'migration',
        }
    )


class Migration(migrations.Migration):

    dependencies = [
        ("crawler_admin", "0008_add_display_error_zh_hans"),
    ]

    operations = [
        # Add language detection fields to TrendItem
        migrations.AddField(
            model_name="trenditem",
            name="base_lang",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Base language code (e.g., 'zh', 'en', 'ja')",
                max_length=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="trenditem",
            name="locale",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Full locale/region tag (e.g., 'zh-Hans', 'en-US')",
                max_length=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="trenditem",
            name="lang_group",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Language grouping key for feeds/ranking (zh-Hans/zh-Hant → 'zh')",
                max_length=10,
                null=True,
            ),
        ),

        # Add hotness scoring field
        migrations.AddField(
            model_name="trenditem",
            name="hotness",
            field=models.FloatField(
                blank=True,
                db_index=True,
                help_text="Computed hotness score (recency × engagement)",
                null=True,
            ),
        ),

        # Add metadata timestamp fields
        migrations.AddField(
            model_name="trenditem",
            name="lang_detected_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When language detection was performed",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="trenditem",
            name="hotness_computed_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When hotness score was last computed",
                null=True,
            ),
        ),

        # Create SystemSettings model
        migrations.CreateModel(
            name="SystemSettings",
            fields=[
                (
                    "key",
                    models.CharField(
                        help_text="Unique setting identifier",
                        max_length=100,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "value_json",
                    models.JSONField(
                        help_text="Setting value (can be int, float, bool, string, or JSON object)"
                    ),
                ),
                (
                    "description",
                    models.TextField(
                        blank=True,
                        help_text="Human-readable description of this setting",
                    ),
                ),
                (
                    "value_type",
                    models.CharField(
                        choices=[
                            ("integer", "Integer"),
                            ("float", "Float"),
                            ("boolean", "Boolean"),
                            ("string", "String"),
                            ("json", "JSON Object"),
                        ],
                        default="integer",
                        help_text="Type hint for admin UI",
                        max_length=20,
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                        help_text="When this setting was last modified",
                    ),
                ),
                (
                    "updated_by",
                    models.CharField(
                        blank=True,
                        help_text="Who last modified this setting (username or system)",
                        max_length=100,
                    ),
                ),
            ],
            options={
                "verbose_name": "System Setting",
                "verbose_name_plural": "System Settings",
                "ordering": ["key"],
            },
        ),

        # Create ItemDerivation model
        migrations.CreateModel(
            name="ItemDerivation",
            fields=[
                (
                    "derivation_id",
                    models.AutoField(primary_key=True, serialize=False),
                ),
                (
                    "derivation_type",
                    models.CharField(
                        choices=[
                            ("translation", "Translation"),
                            ("summary", "AI Summary"),
                            ("embedding", "Semantic Embedding"),
                        ],
                        default="translation",
                        help_text="Type of derived content",
                        max_length=20,
                    ),
                ),
                (
                    "target_locale",
                    models.CharField(
                        help_text="Target locale for this derivation (e.g., 'zh-Hans')",
                        max_length=10,
                    ),
                ),
                (
                    "content_title",
                    models.TextField(help_text="Derived title content"),
                ),
                (
                    "content_body",
                    models.TextField(
                        blank=True,
                        help_text="Derived body/description content",
                        null=True,
                    ),
                ),
                (
                    "engine",
                    models.CharField(
                        help_text="Engine/provider used (e.g., 'deepl', 'openai')",
                        max_length=20,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("complete", "Complete"),
                            ("failed", "Failed"),
                        ],
                        default="complete",
                        help_text="Processing status",
                        max_length=20,
                    ),
                ),
                (
                    "error_message",
                    models.TextField(
                        blank=True,
                        help_text="Error message if processing failed",
                        null=True,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        help_text="When this derivation was created",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                        help_text="When this derivation was last updated",
                    ),
                ),
                (
                    "item",
                    models.ForeignKey(
                        help_text="Original item this derivation belongs to",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="derivations",
                        to="crawler_admin.trenditem",
                    ),
                ),
            ],
            options={
                "verbose_name": "Item Derivation",
                "verbose_name_plural": "Item Derivations",
                "ordering": ["-created_at"],
            },
        ),

        # Add indexes for language-aware queries
        migrations.AddIndex(
            model_name="trenditem",
            index=models.Index(
                fields=["lang_group", "-hotness"],
                name="crawler_admin_trenditem_lang_group_hotness_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="trenditem",
            index=models.Index(
                fields=["base_lang", "-collected_at"],
                name="crawler_admin_trenditem_base_lang_collected_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="trenditem",
            index=models.Index(
                fields=["hotness"],
                name="crawler_admin_trenditem_hotness_idx",
            ),
        ),

        # Add indexes for ItemDerivation
        migrations.AddIndex(
            model_name="itemderivation",
            index=models.Index(
                fields=["item", "derivation_type", "target_locale"],
                name="crawler_admin_itemderivation_lookup_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="itemderivation",
            index=models.Index(
                fields=["derivation_type", "status"],
                name="crawler_admin_itemderivation_type_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="itemderivation",
            index=models.Index(
                fields=["target_locale", "status"],
                name="crawler_admin_itemderivation_locale_status_idx",
            ),
        ),

        # Add unique constraint for ItemDerivation
        migrations.AlterUniqueTogether(
            name="itemderivation",
            unique_together={("item", "derivation_type", "target_locale")},
        ),

        # Create initial system settings
        migrations.RunPython(create_initial_system_settings, migrations.RunPython.noop),
    ]
