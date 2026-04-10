# Generated 2026-04-10 — Phase 2: topic classification fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crawler_admin", "0022_trenditem_region_classified_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="trenditem",
            name="topic_tags",
            field=models.JSONField(
                default=list,
                blank=True,
                help_text=(
                    "Topic labels for story selection "
                    "(e.g. [\"politics\",\"finance\",\"ai\"]). "
                    "Set by topic_classifier_worker. "
                    "Vocabulary: politics, finance, ai, tech, science, "
                    "entertainment, sports, business, crime, society, "
                    "health, environment."
                ),
            ),
        ),
        migrations.AddField(
            model_name="trenditem",
            name="topic_classified_at",
            field=models.DateTimeField(
                null=True,
                blank=True,
                db_index=True,
                help_text=(
                    "When topic classification was last attempted "
                    "(heuristic or LLM). NULL = never tried."
                ),
            ),
        ),
    ]
