"""
Django management command to setup sample RSS sources.

Feature B (from /tmp/t3):
Automatically configures sample RSS feeds using the generic_rss collector.

Usage:
    python manage.py setup_rss_sources
    python manage.py setup_rss_sources --dry-run
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from crawler_admin.models import Region, TrendSurface


class Command(BaseCommand):
    help = 'Setup sample RSS sources using generic_rss collector'

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
        self.stdout.write(self.style.SUCCESS('  Setup Generic RSS Sources'))
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
        self.stdout.write('→ Configuring RSS sources...')
        self.stdout.write('')

        # Define RSS sources to setup
        rss_sources = [
            {
                'key': 'techcrunch_rss',
                'region': region_us,
                'platform': 'rss',
                'surface_type': 'rss_feed',
                'bucket': 'news',
                'bucket_weight': 1.0,
                'enabled': True,
                'poll_interval_seconds': 1800,  # 30 minutes
                'max_items_per_run': 30,
                'entrypoint': 'crawler_api.surfaces.generic_rss:collect',
                'config_json': {
                    'feed_urls': [
                        'https://techcrunch.com/feed/'
                    ],
                    'locale': 'en-US',
                    'source_name': 'techcrunch'
                }
            },
            {
                'key': 'arstechnica_rss',
                'region': region_us,
                'platform': 'rss',
                'surface_type': 'rss_feed',
                'bucket': 'news',
                'bucket_weight': 1.0,
                'enabled': True,
                'poll_interval_seconds': 1800,  # 30 minutes
                'max_items_per_run': 30,
                'entrypoint': 'crawler_api.surfaces.generic_rss:collect',
                'config_json': {
                    'feed_urls': [
                        'https://feeds.arstechnica.com/arstechnica/index'
                    ],
                    'locale': 'en-US',
                    'source_name': 'arstechnica'
                }
            },
            {
                'key': 'theverge_rss',
                'region': region_us,
                'platform': 'rss',
                'surface_type': 'rss_feed',
                'bucket': 'news',
                'bucket_weight': 1.0,
                'enabled': True,
                'poll_interval_seconds': 1800,  # 30 minutes
                'max_items_per_run': 30,
                'entrypoint': 'crawler_api.surfaces.generic_rss:collect',
                'config_json': {
                    'feed_urls': [
                        'https://www.theverge.com/rss/index.xml'
                    ],
                    'locale': 'en-US',
                    'source_name': 'theverge'
                }
            },
        ]

        # Track statistics
        created_count = 0
        updated_count = 0
        skipped_count = 0

        with transaction.atomic():
            for config in rss_sources:
                key = config['key']

                # Check if exists
                existing = TrendSurface.objects.filter(
                    region=region_us,
                    key=key
                ).first()

                if existing:
                    # Already exists
                    status = 'ℹ EXISTS'
                    skipped_count += 1
                else:
                    # Create new
                    if not dry_run:
                        TrendSurface.objects.create(**config)

                    status = '✓ NEW'
                    created_count += 1

                # Display
                self.stdout.write(
                    f"  {status:20s} {key:30s} {config['platform']:15s} "
                    f"{config['bucket']:10s}"
                )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('─' * 60))
        self.stdout.write(self.style.SUCCESS('Summary:'))
        self.stdout.write(f"  ✓ Created:  {created_count}")
        self.stdout.write(f"  ⚠ Updated:  {updated_count}")
        self.stdout.write(f"  ℹ Skipped:  {skipped_count}")
        self.stdout.write(self.style.SUCCESS('─' * 60))

        if dry_run:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING('DRY RUN - No changes were saved'))
        else:
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS('✓ RSS sources configured successfully!'))
            self.stdout.write('')
            self.stdout.write('Next steps:')
            self.stdout.write('  1. Restart services: ./setup.sh restart')
            self.stdout.write('  2. Test collection: ./setup.sh (option 6: Force Collection Run)')

        self.stdout.write('')
