"""
Migration to add ProviderHealth model for tracking translation provider status.

This model tracks:
- Provider availability states (available, unavailable_*)
- Error details (last error message, code, timestamps)
- Failure counters for monitoring

Data migration creates initial rows for 'deepl' and 'openai' providers.
"""

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('translation', '0003_add_top_p_field'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProviderHealth',
            fields=[
                ('provider', models.CharField(
                    choices=[('deepl', 'DeepL'), ('openai', 'OpenAI')],
                    help_text='Translation provider identifier',
                    max_length=20,
                    primary_key=True,
                    serialize=False
                )),
                ('state', models.CharField(
                    choices=[
                        ('available', 'Available'),
                        ('unavailable_funds', 'Unavailable - Quota/Billing'),
                        ('unavailable_auth', 'Unavailable - Authentication'),
                        ('unavailable_rate_limit', 'Unavailable - Rate Limited'),
                        ('unavailable_transient', 'Unavailable - Transient Error'),
                    ],
                    default='available',
                    help_text='Current health state of the provider',
                    max_length=30
                )),
                ('last_error_message', models.TextField(
                    blank=True,
                    help_text='Last error message encountered',
                    null=True
                )),
                ('last_error_code', models.CharField(
                    blank=True,
                    help_text="Last error code (e.g., 'insufficient_quota', '401')",
                    max_length=50,
                    null=True
                )),
                ('last_state_change_at', models.DateTimeField(
                    auto_now=True,
                    help_text='When the state last changed'
                )),
                ('last_success_at', models.DateTimeField(
                    blank=True,
                    help_text='Last successful translation timestamp',
                    null=True
                )),
                ('consecutive_failures', models.IntegerField(
                    default=0,
                    help_text='Number of consecutive failures'
                )),
            ],
            options={
                'verbose_name': 'Provider Health',
                'verbose_name_plural': 'Provider Health Status',
            },
        ),
        # Data migration: Create initial provider health records
        migrations.RunPython(
            code=lambda apps, schema_editor: _create_initial_providers(apps, schema_editor),
            reverse_code=lambda apps, schema_editor: None,
        ),
    ]


def _create_initial_providers(apps, schema_editor):
    """Create initial provider health records for deepl and openai."""
    ProviderHealth = apps.get_model('translation', 'ProviderHealth')

    # Create default provider records (available state)
    for provider in ['deepl', 'openai']:
        ProviderHealth.objects.get_or_create(
            provider=provider,
            defaults={
                'state': 'available',
                'consecutive_failures': 0,
            }
        )
