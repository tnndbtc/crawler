"""
Add story_category, classification_state, classification_version fields to TrendItem.
Alter classified_by max_length from 16 to 32 and update help_text.

story_category:        canonical 9-value vocabulary for story engine consumption
classification_state:  explicit pipeline state (pending/heuristic_complete/llm_complete/failed)
classification_version: composite version token (<taxonomy_version>:<keyword_map_sha[:8]>)
classified_by:         expanded from 'heuristic'|'llm' to signal-level
                       bucket|platform|keyword|locale|surface|llm
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crawler_admin', '0025_add_classified_by'),
    ]

    operations = [
        migrations.AlterField(
            model_name='trenditem',
            name='classified_by',
            field=models.CharField(
                max_length=32,
                null=True,
                blank=True,
                help_text=(
                    "Which signal layer produced story_category: "
                    "bucket | platform | keyword | locale | surface | llm. "
                    "NULL = not yet classified."
                ),
            ),
        ),
        migrations.AddField(
            model_name='trenditem',
            name='story_category',
            field=models.CharField(
                max_length=16,
                null=True,
                blank=True,
                db_index=True,
                help_text=(
                    "Canonical story category for story engine selection. "
                    "Vocabulary (9 values): world | politics | business | technology | "
                    "ai | science | society | sports | entertainment. "
                    "NULL = pending or failed classification. "
                    "Set by topic_classifier_worker."
                ),
            ),
        ),
        migrations.AddField(
            model_name='trenditem',
            name='classification_state',
            field=models.CharField(
                max_length=20,
                default='pending',
                db_index=True,
                help_text=(
                    "Classification pipeline state: "
                    "pending | heuristic_complete | llm_complete | failed. "
                    "Replaces the topic_classified_at IS NULL proxy for 'pending'. "
                    "Set by topic_classifier_worker."
                ),
            ),
        ),
        migrations.AddField(
            model_name='trenditem',
            name='classification_version',
            field=models.CharField(
                max_length=32,
                null=True,
                blank=True,
                help_text=(
                    "Version token identifying which keyword map and taxonomy produced "
                    "this classification. Format: '<taxonomy_version>:<keyword_map_sha[:8]>'. "
                    "Example: '1:a3f2c8d1'. NULL = legacy item not yet re-processed. "
                    "Set by topic_classifier_worker after each successful classification."
                ),
            ),
        ),
    ]
