# Generated migration - add 'queued' choice to summary_status on TrendItem

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crawler_admin", "0015_per_locale_hot_percent"),
    ]

    operations = [
        migrations.AlterField(
            model_name="trenditem",
            name="summary_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("queued", "Queued for summarization (selected for translation)"),
                    ("complete", "Complete"),
                    ("failed", "Failed"),
                    ("skipped", "Skipped (no summarizable content)"),
                ],
                default="pending",
                help_text="AI summarization status for description_original",
                max_length=20,
            ),
        ),
    ]
