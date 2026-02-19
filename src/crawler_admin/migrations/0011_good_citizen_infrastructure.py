"""
Migration to add Good Citizen infrastructure.

Implements resilience patterns for HTTP requests to reduce block risk:

Phase 1: Database Models
1. DomainPolicy - Per-domain rate limiting and circuit breaker state
2. URLCache - HTTP caching metadata (ETag, Last-Modified)
3. DomainMetrics - Time-series observability data

Architecture:
- Mirrors ProviderHealth pattern from translation.models
- Enables multi-server coordination via database state
- Supports feature flags via SystemSettings

Feature flags (added to SystemSettings):
- enable_rate_limiting: Default False (disabled during rollout)
- enable_circuit_breaker: Default False (disabled during rollout)
- enable_http_caching: Default False (disabled during rollout)
- default_domain_max_rpm: Default 30 (requests per minute)
- default_domain_max_concurrent: Default 5 (concurrent requests)
"""

from django.db import migrations, models
import django.db.models.deletion
from django.utils import timezone


def add_good_citizen_settings(apps, schema_editor):
    """Add SystemSettings entries for Good Citizen features."""
    SystemSettings = apps.get_model("crawler_admin", "SystemSettings")

    settings = [
        {
            'key': 'enable_rate_limiting',
            'value_json': False,
            'description': 'Enable per-domain rate limiting (requests per minute)',
            'value_type': 'boolean',
            'updated_by': 'migration_0011',
        },
        {
            'key': 'enable_circuit_breaker',
            'value_json': False,
            'description': 'Enable circuit breaker for failing domains (auto-skip on repeated errors)',
            'value_type': 'boolean',
            'updated_by': 'migration_0011',
        },
        {
            'key': 'enable_http_caching',
            'value_json': False,
            'description': 'Enable HTTP caching with ETag/Last-Modified headers',
            'value_type': 'boolean',
            'updated_by': 'migration_0011',
        },
        {
            'key': 'default_domain_max_rpm',
            'value_json': 30,
            'description': 'Default maximum requests per minute per domain',
            'value_type': 'integer',
            'updated_by': 'migration_0011',
        },
        {
            'key': 'default_domain_max_concurrent',
            'value_json': 5,
            'description': 'Default maximum concurrent requests per domain',
            'value_type': 'integer',
            'updated_by': 'migration_0011',
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


def remove_good_citizen_settings(apps, schema_editor):
    """Remove Good Citizen SystemSettings entries."""
    SystemSettings = apps.get_model("crawler_admin", "SystemSettings")

    setting_keys = [
        'enable_rate_limiting',
        'enable_circuit_breaker',
        'enable_http_caching',
        'default_domain_max_rpm',
        'default_domain_max_concurrent',
    ]

    SystemSettings.objects.filter(key__in=setting_keys).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("crawler_admin", "0010_update_translation_settings_multi_language"),
    ]

    operations = [
        # Create DomainPolicy table
        migrations.CreateModel(
            name='DomainPolicy',
            fields=[
                ('domain', models.CharField(
                    help_text="Domain name (e.g., 'news.ycombinator.com')",
                    max_length=255,
                    primary_key=True,
                    serialize=False
                )),
                ('max_rpm', models.IntegerField(
                    default=30,
                    help_text='Requests per minute limit (default: 30)'
                )),
                ('max_concurrent', models.IntegerField(
                    default=5,
                    help_text='Max simultaneous requests (default: 5)'
                )),
                ('circuit_state', models.CharField(
                    choices=[
                        ('closed', 'Closed (Normal)'),
                        ('open', 'Open (Blocked)'),
                        ('half_open', 'Half-Open (Testing)')
                    ],
                    default='closed',
                    help_text='Current circuit breaker state',
                    max_length=20
                )),
                ('circuit_failure_threshold', models.IntegerField(
                    default=5,
                    help_text='Consecutive failures before opening circuit (default: 5)'
                )),
                ('circuit_cooldown_seconds', models.IntegerField(
                    default=300,
                    help_text='Cooldown duration when circuit is open (default: 300s = 5min)'
                )),
                ('consecutive_failures', models.IntegerField(
                    default=0,
                    help_text='Current consecutive failure count'
                )),
                ('current_backoff_multiplier', models.FloatField(
                    default=1.0,
                    help_text='Current backoff multiplier (1.0 = normal, >1.0 = slowed down)'
                )),
                ('backoff_decay_rate', models.FloatField(
                    default=0.1,
                    help_text='How fast backoff recovers on success (default: 0.1 = 10% per success)'
                )),
                ('last_request_at', models.DateTimeField(
                    blank=True,
                    help_text='Last request timestamp (for rate limiting)',
                    null=True
                )),
                ('last_success_at', models.DateTimeField(
                    blank=True,
                    help_text='Last successful request timestamp',
                    null=True
                )),
                ('last_failure_at', models.DateTimeField(
                    blank=True,
                    help_text='Last failure timestamp',
                    null=True
                )),
                ('circuit_opened_at', models.DateTimeField(
                    blank=True,
                    help_text='When circuit was opened (for cooldown calculation)',
                    null=True
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Domain Policy',
                'verbose_name_plural': 'Domain Policies',
                'ordering': ['domain'],
            },
        ),

        # Create URLCache table
        migrations.CreateModel(
            name='URLCache',
            fields=[
                ('url_hash', models.CharField(
                    help_text='SHA256 hash of normalized URL',
                    max_length=64,
                    primary_key=True,
                    serialize=False
                )),
                ('url', models.TextField(help_text='Original URL')),
                ('etag', models.CharField(
                    blank=True,
                    help_text='ETag header value from response',
                    max_length=255,
                    null=True
                )),
                ('last_modified', models.CharField(
                    blank=True,
                    help_text='Last-Modified header value from response',
                    max_length=255,
                    null=True
                )),
                ('expires_at', models.DateTimeField(
                    blank=True,
                    help_text='Cache expiration timestamp (from Cache-Control or Expires header)',
                    null=True
                )),
                ('last_validated_at', models.DateTimeField(
                    auto_now=True,
                    help_text='Last time this cache entry was validated (touched)'
                )),
                ('created_at', models.DateTimeField(
                    auto_now_add=True,
                    help_text='When this cache entry was created'
                )),
            ],
            options={
                'verbose_name': 'URL Cache',
                'verbose_name_plural': 'URL Caches',
                'ordering': ['-last_validated_at'],
            },
        ),

        # Create DomainMetrics table
        migrations.CreateModel(
            name='DomainMetrics',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True,
                    primary_key=True,
                    serialize=False,
                    verbose_name='ID'
                )),
                ('domain', models.CharField(
                    db_index=True,
                    help_text="Domain name (e.g., 'news.ycombinator.com')",
                    max_length=255
                )),
                ('hour_bucket', models.DateTimeField(
                    db_index=True,
                    help_text='Hourly aggregation bucket (truncated to hour)'
                )),
                ('requests_2xx', models.IntegerField(
                    default=0,
                    help_text='Successful requests (200-299)'
                )),
                ('requests_4xx', models.IntegerField(
                    default=0,
                    help_text='Client errors (400-499)'
                )),
                ('requests_5xx', models.IntegerField(
                    default=0,
                    help_text='Server errors (500-599)'
                )),
                ('requests_429', models.IntegerField(
                    default=0,
                    help_text='Rate limit errors (429 Too Many Requests)'
                )),
                ('requests_403', models.IntegerField(
                    default=0,
                    help_text='Forbidden errors (403)'
                )),
                ('latency_p50', models.IntegerField(
                    blank=True,
                    help_text='Median latency in milliseconds',
                    null=True
                )),
                ('latency_p95', models.IntegerField(
                    blank=True,
                    help_text='95th percentile latency in milliseconds',
                    null=True
                )),
                ('retry_count', models.IntegerField(
                    default=0,
                    help_text='Total number of retries performed'
                )),
                ('circuit_open_duration_seconds', models.IntegerField(
                    default=0,
                    help_text='Total seconds circuit was open in this hour'
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Domain Metrics',
                'verbose_name_plural': 'Domain Metrics',
                'ordering': ['-hour_bucket', 'domain'],
            },
        ),

        # Add indexes
        migrations.AddIndex(
            model_name='domainpolicy',
            index=models.Index(fields=['circuit_state'], name='crawler_adm_circuit_idx'),
        ),
        migrations.AddIndex(
            model_name='urlcache',
            index=models.Index(fields=['expires_at'], name='crawler_adm_expires_idx'),
        ),
        migrations.AddIndex(
            model_name='urlcache',
            index=models.Index(fields=['-last_validated_at'], name='crawler_adm_last_va_idx'),
        ),
        migrations.AddIndex(
            model_name='domainmetrics',
            index=models.Index(fields=['domain', '-hour_bucket'], name='crawler_adm_domain_hour_idx'),
        ),
        migrations.AddIndex(
            model_name='domainmetrics',
            index=models.Index(fields=['-hour_bucket'], name='crawler_adm_hour_bu_idx'),
        ),

        # Add unique constraint for DomainMetrics
        migrations.AlterUniqueTogether(
            name='domainmetrics',
            unique_together={('domain', 'hour_bucket')},
        ),

        # Add SystemSettings for feature flags
        migrations.RunPython(
            add_good_citizen_settings,
            reverse_code=remove_good_citizen_settings,
        ),
    ]
