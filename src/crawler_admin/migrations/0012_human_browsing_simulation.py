"""
Migration to add Human Browsing Simulation infrastructure.

Implements behavior shaping layer to reduce bot detection:

Phase 1: Database Schema
1. DomainPolicy - Add simulation configuration and penalty tracking
2. DomainMetrics - Add direct/simulated request counters

New fields:
- DomainPolicy.simulation_config: Per-domain configuration overrides (JSON)
- DomainPolicy.simulation_penalty_until: Adaptive backoff expiration
- DomainPolicy.simulation_delay_multiplier: Delay scaling during penalty
- DomainPolicy.simulation_probability_multiplier: Probability scaling during penalty
- DomainMetrics.direct_requests: Normal crawler requests
- DomainMetrics.simulated_requests: Human-like behavior requests
- DomainMetrics.simulation_fallbacks: Failed simulation attempts
- DomainMetrics.simulation_budget_blocked: Budget enforcement blocks

SystemSettings:
- crawler_human_mode_enabled: Master switch (default: False)
- crawler_human_homepage_probability: Homepage visit probability (default: 0.20)
- crawler_human_prefetch_probability: Favicon prefetch probability (default: 0.15)
- crawler_human_base_allowance: Base budget per domain per hour (default: 1)
- crawler_human_backoff_duration_minutes: Penalty duration (default: 15)

Safety constraints:
- ALL requests still flow through RateLimitedClient
- TRUE 30% request increase hard cap enforced by budget
- No concurrency changes
- Minimal additions only
"""

from django.db import migrations, models


def add_human_simulation_settings(apps, schema_editor):
    """Add SystemSettings entries for Human Browsing Simulation."""
    SystemSettings = apps.get_model("crawler_admin", "SystemSettings")

    settings = [
        {
            'key': 'crawler_human_mode_enabled',
            'value_json': False,
            'description': 'Enable human browsing simulation (homepage visits, delays, prefetch)',
            'value_type': 'boolean',
            'updated_by': 'migration_0012',
        },
        {
            'key': 'crawler_human_homepage_probability',
            'value_json': 0.20,
            'description': 'Probability of visiting homepage before article fetch (0.0-1.0)',
            'value_type': 'float',
            'updated_by': 'migration_0012',
        },
        {
            'key': 'crawler_human_prefetch_probability',
            'value_json': 0.15,
            'description': 'Probability of prefetching favicon (0.0-1.0)',
            'value_type': 'float',
            'updated_by': 'migration_0012',
        },
        {
            'key': 'crawler_human_base_allowance',
            'value_json': 1,
            'description': 'Base simulation budget per domain per hour (minimum allowed simulated requests)',
            'value_type': 'integer',
            'updated_by': 'migration_0012',
        },
        {
            'key': 'crawler_human_backoff_duration_minutes',
            'value_json': 15,
            'description': 'Duration of adaptive backoff penalty in minutes (after 429/403 errors)',
            'value_type': 'integer',
            'updated_by': 'migration_0012',
        },
    ]

    for setting in settings:
        SystemSettings.objects.update_or_create(
            key=setting['key'],
            defaults={
                'value_json': setting['value_json'],
                'description': setting['description'],
                'value_type': setting['value_type'],
                'updated_by': setting['updated_by'],
            }
        )


def remove_human_simulation_settings(apps, schema_editor):
    """Remove Human Browsing Simulation SystemSettings entries."""
    SystemSettings = apps.get_model("crawler_admin", "SystemSettings")

    setting_keys = [
        'crawler_human_mode_enabled',
        'crawler_human_homepage_probability',
        'crawler_human_prefetch_probability',
        'crawler_human_base_allowance',
        'crawler_human_backoff_duration_minutes',
    ]

    SystemSettings.objects.filter(key__in=setting_keys).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("crawler_admin", "0011_good_citizen_infrastructure"),
    ]

    operations = [
        # Add human browsing simulation fields to DomainPolicy
        migrations.AddField(
            model_name='domainpolicy',
            name='simulation_config',
            field=models.JSONField(
                blank=True,
                help_text='Per-domain simulation configuration overrides',
                null=True
            ),
        ),
        migrations.AddField(
            model_name='domainpolicy',
            name='simulation_penalty_until',
            field=models.DateTimeField(
                blank=True,
                help_text='Adaptive backoff penalty expiration (null = no penalty active)',
                null=True
            ),
        ),
        migrations.AddField(
            model_name='domainpolicy',
            name='simulation_delay_multiplier',
            field=models.FloatField(
                default=1.0,
                help_text='Delay multiplier during penalty (default: 1.0 = normal, 2.5 = slower)'
            ),
        ),
        migrations.AddField(
            model_name='domainpolicy',
            name='simulation_probability_multiplier',
            field=models.FloatField(
                default=1.0,
                help_text='Probability multiplier during penalty (default: 1.0 = normal, 0.5 = reduced)'
            ),
        ),

        # Add human browsing simulation metrics to DomainMetrics
        migrations.AddField(
            model_name='domainmetrics',
            name='direct_requests',
            field=models.IntegerField(
                default=0,
                help_text='Normal crawler requests (actual content fetches)'
            ),
        ),
        migrations.AddField(
            model_name='domainmetrics',
            name='simulated_requests',
            field=models.IntegerField(
                default=0,
                help_text='Human-like simulation requests (homepage visits, favicon prefetch)'
            ),
        ),
        migrations.AddField(
            model_name='domainmetrics',
            name='simulation_fallbacks',
            field=models.IntegerField(
                default=0,
                help_text='Failed simulation attempts (errors, timeouts)'
            ),
        ),
        migrations.AddField(
            model_name='domainmetrics',
            name='simulation_budget_blocked',
            field=models.IntegerField(
                default=0,
                help_text='Simulation requests blocked by budget enforcement'
            ),
        ),

        # Add SystemSettings for human browsing simulation
        migrations.RunPython(
            add_human_simulation_settings,
            reverse_code=remove_human_simulation_settings,
        ),
    ]
