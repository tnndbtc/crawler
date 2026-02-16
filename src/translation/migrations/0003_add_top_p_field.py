# Generated migration for adding top_p field to LLMModelConfig

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('translation', '0002_initial_data'),
    ]

    operations = [
        migrations.AddField(
            model_name='llmmodelconfig',
            name='top_p',
            field=models.FloatField(
                default=1.0,
                help_text='Top-p (nucleus) sampling (0.0-1.0, lower = more focused)',
            ),
        ),
    ]
