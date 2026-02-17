# Generated manually - Add display_error_zh_hans field for error tracking

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crawler_admin", "0007_remove_translationsettings"),
    ]

    operations = [
        migrations.AddField(
            model_name="trenditem",
            name="display_error_zh_hans",
            field=models.TextField(
                blank=True,
                help_text="Error message for zh-Hans display translation",
                null=True,
            ),
        ),
    ]
