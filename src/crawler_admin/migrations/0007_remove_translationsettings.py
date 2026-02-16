# Generated manually - Remove deprecated TranslationSettings model

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("crawler_admin", "0006_add_translation_fields"),
    ]

    operations = [
        migrations.DeleteModel(
            name="TranslationSettings",
        ),
    ]
