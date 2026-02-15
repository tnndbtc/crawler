"""
Django management command to setup migrated collectors.

Automatically configures 14 collectors migrated from the source project.

Usage:
    python manage.py setup_migrated_collectors
    python manage.py setup_migrated_collectors --dry-run
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from crawler_admin.models import Region, TrendSurface


class Command(BaseCommand):
    help = 'Setup 14 migrated collectors from source project'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without saving to database',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be saved\n'))

        self.stdout.write(self.style.SUCCESS('═' * 60))
        self.stdout.write(self.style.SUCCESS('  Setup Migrated Collectors'))
        self.stdout.write(self.style.SUCCESS('═' * 60))
        self.stdout.write('')

        # Check prerequisites
        self.stdout.write('→ Checking prerequisites...')

        try:
            region_us = Region.objects.get(key='us')
            self.stdout.write(self.style.SUCCESS(f"✓ Region 'us' exists"))
        except Region.DoesNotExist:
            raise CommandError(
                "Region 'us' not found. Please create it first or run initial setup."
            )

        self.stdout.write('')
        self.stdout.write('→ Configuring collectors...')
        self.stdout.write('')

        # Define all collectors to setup
        collectors_config = self._get_collectors_config(region_us)

        # Track statistics
        created_count = 0
        updated_count = 0
        skipped_count = 0

        # Group by category for display
        news_collectors = []
        social_collectors = []

        with transaction.atomic():
            for config in collectors_config:
                key = config['key']
                category = config.pop('_category')

                # Check if exists
                existing = TrendSurface.objects.filter(
                    region=region_us,
                    key=key
                ).first()

                if existing:
                    # Update existing
                    if self._should_update(existing, config):
                        if not dry_run:
                            for field, value in config.items():
                                setattr(existing, field, value)
                            existing.save()

                        status = '⚠ UPDATED'
                        updated_count += 1
                    else:
                        status = 'ℹ EXISTS (no changes)'
                        skipped_count += 1
                else:
                    # Create new
                    if not dry_run:
                        TrendSurface.objects.create(**config)

                    status = '✓ NEW'
                    created_count += 1

                # Add to display lists
                display_info = {
                    'key': key,
                    'platform': config['platform'],
                    'bucket': config['bucket'],
                    'interval': config['poll_interval_seconds'],
                    'status': status
                }

                if category == 'news':
                    news_collectors.append(display_info)
                else:
                    social_collectors.append(display_info)

            if dry_run:
                # Rollback in dry-run mode
                transaction.set_rollback(True)

        # Display results
        self._display_collectors('News Sources', news_collectors)
        self._display_collectors('Social Media Sources', social_collectors)

        # Summary
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('═' * 60))

        if dry_run:
            self.stdout.write(self.style.WARNING(f'DRY RUN: Would configure {created_count + updated_count} collectors'))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'✓ Successfully configured {created_count + updated_count} collectors!'
            ))

        self.stdout.write(f'  • Created: {created_count}')
        self.stdout.write(f'  • Updated: {updated_count}')
        self.stdout.write(f'  • Skipped: {skipped_count}')
        self.stdout.write('')

        # Notes
        if not dry_run:
            self.stdout.write(self.style.WARNING('ℹ Notes:'))

            # Check for YouTube
            if TrendSurface.objects.filter(key='youtube_trending').exists():
                self.stdout.write('  • YouTube collector now has dual-mode configuration option')

            # Check for Twitter
            if TrendSurface.objects.filter(key='twitter_trending').exists():
                self.stdout.write('  • Twitter collector requires TWITTER_BEARER_TOKEN in .env')

            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS('Next steps:'))
            self.stdout.write('  1. Restart services: ./setup.sh restart')
            self.stdout.write('  2. Check Django Admin: http://localhost:8001/admin/crawler_admin/trendsurface/')
            self.stdout.write('  3. Monitor bucket distribution (no bucket should exceed 40%)')
            self.stdout.write('')

    def _get_collectors_config(self, region):
        """Define all collector configurations."""
        return [
            # News collectors - region_local
            {
                'region': region,
                'key': 'bbc_news',
                'platform': 'bbc',
                'surface_type': 'news',
                'bucket': 'region_local',
                'bucket_weight': 1.0,
                'entrypoint': 'crawler_api.surfaces.bbc_news:collect',
                'enabled': True,
                'poll_interval_seconds': 1200,  # 20 min
                'max_items_per_run': 40,
                'config_json': {'locale': 'en-GB'},
                '_category': 'news',
            },
            {
                'region': region,
                'key': 'google_news',
                'platform': 'google_news',
                'surface_type': 'news',
                'bucket': 'region_local',
                'bucket_weight': 1.0,
                'entrypoint': 'crawler_api.surfaces.google_news:collect',
                'enabled': True,
                'poll_interval_seconds': 900,  # 15 min
                'max_items_per_run': 50,
                'config_json': {'locale': 'en-US'},
                '_category': 'news',
            },
            {
                'region': region,
                'key': 'reuters_news',
                'platform': 'reuters',
                'surface_type': 'news',
                'bucket': 'region_local',
                'bucket_weight': 1.0,
                'entrypoint': 'crawler_api.surfaces.reuters_news:collect',
                'enabled': True,
                'poll_interval_seconds': 900,  # 15 min
                'max_items_per_run': 40,
                'config_json': {'locale': 'en-US'},
                '_category': 'news',
            },
            {
                'region': region,
                'key': 'ap_news',
                'platform': 'ap',
                'surface_type': 'news',
                'bucket': 'region_local',
                'bucket_weight': 1.0,
                'entrypoint': 'crawler_api.surfaces.ap_news:collect',
                'enabled': True,
                'poll_interval_seconds': 1200,  # 20 min
                'max_items_per_run': 40,
                'config_json': {'locale': 'en-US'},
                '_category': 'news',
            },
            {
                'region': region,
                'key': 'guardian_news',
                'platform': 'guardian',
                'surface_type': 'news',
                'bucket': 'region_local',
                'bucket_weight': 1.0,
                'entrypoint': 'crawler_api.surfaces.guardian_news:collect',
                'enabled': True,
                'poll_interval_seconds': 1200,  # 20 min
                'max_items_per_run': 40,
                'config_json': {'locale': 'en-GB'},
                '_category': 'news',
            },
            {
                'region': region,
                'key': 'aljazeera_news',
                'platform': 'aljazeera',
                'surface_type': 'news',
                'bucket': 'region_local',
                'bucket_weight': 1.0,
                'entrypoint': 'crawler_api.surfaces.aljazeera_news:collect',
                'enabled': True,
                'poll_interval_seconds': 1200,  # 20 min
                'max_items_per_run': 40,
                'config_json': {'locale': 'en-US'},
                '_category': 'news',
            },
            # Entertainment news
            {
                'region': region,
                'key': 'billboard_news',
                'platform': 'billboard',
                'surface_type': 'news',
                'bucket': 'category_entertainment',
                'bucket_weight': 1.0,
                'entrypoint': 'crawler_api.surfaces.billboard_news:collect',
                'enabled': True,
                'poll_interval_seconds': 1800,  # 30 min
                'max_items_per_run': 30,
                'config_json': {'locale': 'en-US'},
                '_category': 'news',
            },
            {
                'region': region,
                'key': 'variety_news',
                'platform': 'variety',
                'surface_type': 'news',
                'bucket': 'category_entertainment',
                'bucket_weight': 1.0,
                'entrypoint': 'crawler_api.surfaces.variety_news:collect',
                'enabled': True,
                'poll_interval_seconds': 1800,  # 30 min
                'max_items_per_run': 30,
                'config_json': {'locale': 'en-US'},
                '_category': 'news',
            },
            # Gaming news
            {
                'region': region,
                'key': 'ign_news',
                'platform': 'ign',
                'surface_type': 'news',
                'bucket': 'category_gaming',
                'bucket_weight': 1.0,
                'entrypoint': 'crawler_api.surfaces.ign_news:collect',
                'enabled': True,
                'poll_interval_seconds': 1800,  # 30 min
                'max_items_per_run': 30,
                'config_json': {'locale': 'en-US'},
                '_category': 'news',
            },
            {
                'region': region,
                'key': 'polygon_news',
                'platform': 'polygon',
                'surface_type': 'news',
                'bucket': 'category_gaming',
                'bucket_weight': 1.0,
                'entrypoint': 'crawler_api.surfaces.polygon_news:collect',
                'enabled': True,
                'poll_interval_seconds': 1800,  # 30 min
                'max_items_per_run': 30,
                'config_json': {'locale': 'en-US'},
                '_category': 'news',
            },
            # Social media collectors
            {
                'region': region,
                'key': 'hackernews',
                'platform': 'hackernews',
                'surface_type': 'ranking',
                'bucket': 'category_tech',
                'bucket_weight': 1.0,
                'entrypoint': 'crawler_api.surfaces.hackernews:collect',
                'enabled': True,
                'poll_interval_seconds': 1200,  # 20 min
                'max_items_per_run': 30,
                'config_json': {},
                '_category': 'social',
            },
            {
                'region': region,
                'key': 'google_trends',
                'platform': 'google_trends',
                'surface_type': 'trending',
                'bucket': 'hot_now',
                'bucket_weight': 1.0,
                'entrypoint': 'crawler_api.surfaces.google_trends:collect',
                'enabled': True,
                'poll_interval_seconds': 10800,  # 3 hours
                'max_items_per_run': 30,
                'config_json': {
                    'geo': 'US',
                    'locale': 'en-US',
                    'max_daily': 20,
                    'max_realtime': 10,
                    'include_realtime': True
                },
                '_category': 'social',
            },
            # YouTube - update with dual-mode option
            {
                'region': region,
                'key': 'youtube_trending',
                'platform': 'youtube',
                'surface_type': 'ranking',
                'bucket': 'category_entertainment',
                'bucket_weight': 1.0,
                'entrypoint': 'crawler_api.surfaces.youtube_trending:collect',
                'enabled': True,
                'poll_interval_seconds': 7200,  # 2 hours
                'max_items_per_run': 50,
                'config_json': {
                    'region_code': 'US',
                    'locale': 'en-US',
                    'dual_mode': False  # Can be enabled for dual-mode fetching
                },
                '_category': 'social',
            },
            # Twitter - placeholder (needs API key)
            {
                'region': region,
                'key': 'twitter_trending',
                'platform': 'twitter',
                'surface_type': 'trending',
                'bucket': 'rising',
                'bucket_weight': 1.0,
                'entrypoint': 'crawler_api.surfaces.twitter_trending:collect',
                'enabled': False,  # Disabled until API key is added
                'poll_interval_seconds': 3600,  # 1 hour
                'max_items_per_run': 100,
                'config_json': {
                    'locale': 'en-US',
                    'min_engagement': 100,
                    'hours_back': 6
                },
                '_category': 'social',
            },
        ]

    def _should_update(self, existing, new_config):
        """Check if existing surface needs updates."""
        # Only update if significant fields changed
        important_fields = [
            'entrypoint', 'bucket', 'poll_interval_seconds',
            'max_items_per_run', 'config_json'
        ]

        for field in important_fields:
            if field in new_config:
                if getattr(existing, field) != new_config[field]:
                    return True

        return False

    def _display_collectors(self, title, collectors):
        """Display collector list nicely."""
        if not collectors:
            return

        self.stdout.write(f'\n{title} ({len(collectors)}):')

        for collector in collectors:
            # Format interval
            interval_sec = collector['interval']
            if interval_sec < 3600:
                interval_str = f"{interval_sec // 60}min"
            else:
                interval_str = f"{interval_sec // 3600}h"

            self.stdout.write(
                f"  {collector['status']} {collector['key']:<20} "
                f"({collector['platform']}, {collector['bucket']}, every {interval_str})"
            )
