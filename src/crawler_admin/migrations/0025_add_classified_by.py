"""
Add classified_by field to TrendItem.

Records whether topic_tags were set by 'heuristic' or 'llm'.
NULL = not yet classified, or classified before this field was added.

Used by keyword_harvest_worker to source only LLM-rescued items,
preventing heuristic-rescued items from polluting keyword learning.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crawler_admin', '0024_add_selection_weight'),
    ]

    operations = [
        migrations.AddField(
            model_name='trenditem',
            name='classified_by',
            field=models.CharField(
                max_length=16,
                null=True,
                blank=True,
                help_text=(
                    "Which classifier set topic_tags: 'heuristic' or 'llm'. "
                    "NULL = not yet classified, or classified before this field existed. "
                    "Used by keyword_harvest_worker to source only LLM-rescued items."
                ),
            ),
        ),
    ]
