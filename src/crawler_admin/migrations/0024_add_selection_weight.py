"""
Add selection_weight field to TrendSurface.

Phase 4: Allows per-surface story engine weight to be managed via admin UI.
The story engine selector reads this value and uses it as the surface
multiplier when no story_mix.json surface_weight_overrides entry is set
for that surface key (D2 merge: json override takes precedence).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crawler_admin', '0023_add_topic_tags'),
    ]

    operations = [
        migrations.AddField(
            model_name='trendsurface',
            name='selection_weight',
            field=models.FloatField(
                default=1.0,
                help_text=(
                    'Story engine surface weight (1.0 = normal, >1 = more likely to be selected, '
                    '<1 = less likely). Overridden by story_mix.json surface_weight_overrides if set.'
                ),
            ),
        ),
    ]
