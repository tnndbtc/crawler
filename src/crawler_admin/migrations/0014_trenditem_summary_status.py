# Generated migration - add summary_status to TrendItem

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crawler_admin", "0013_rename_crawler_adm_domain_hour_idx_crawler_adm_domain_d938a0_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="trenditem",
            name="summary_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("complete", "Complete"),
                    ("failed", "Failed"),
                    ("skipped", "Skipped (no summarizable content)"),
                ],
                default="pending",
                help_text="AI summarization status for description_original",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="trenditem",
            name="description_original",
            field=models.TextField(
                blank=True,
                null=True,
                help_text="AI-generated summary (repurposed from raw snippet). Raw content preserved in raw_payload.",
            ),
        ),
    ]
