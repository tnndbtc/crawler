"""
Management command: check_surface_health

Scans all enabled surfaces and auto-disables any that have produced zero
new items across all CrawlRuns in the last 7 days.

Usage:
    python manage.py check_surface_health
    python manage.py check_surface_health --dry-run
    python manage.py check_surface_health --days 14

Cron (daily at 06:00):
    0 6 * * * /path/to/venv/bin/python /path/to/manage.py check_surface_health
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from crawler_admin.models import TrendSurface, CrawlRun


class Command(BaseCommand):
    help = "Auto-disable surfaces with zero stored items in the last N days."

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=7,
            help='Look-back window in days (default: 7)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would be disabled without making changes',
        )

    def handle(self, *args, **options):
        days = options['days']
        dry_run = options['dry_run']
        cutoff = timezone.now() - timedelta(days=days)

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"DRY RUN — no changes will be made"
            ))

        self.stdout.write(f"Checking surface health (last {days} days)...\n")

        enabled_surfaces = TrendSurface.objects.filter(enabled=True).order_by('key')
        disabled_count = 0
        healthy_count = 0

        for surface in enabled_surfaces:
            # Check if any CrawlRun in the window stored at least 1 new item
            had_success = CrawlRun.objects.filter(
                surface=surface,
                started_at__gte=cutoff,
                stored_new_count__gt=0,
            ).exists()

            if had_success:
                healthy_count += 1
                continue

            # Check if the surface has run at all in the window
            # (new surfaces with no runs yet should not be disabled)
            has_any_run = CrawlRun.objects.filter(
                surface=surface,
                started_at__gte=cutoff,
            ).exists()

            if not has_any_run:
                self.stdout.write(
                    f"  SKIP  {surface.key} — no runs in window (may be new or paused)"
                )
                continue

            # Surface has runs but all returned 0 items — disable it
            disabled_count += 1

            if dry_run:
                self.stdout.write(self.style.WARNING(
                    f"  WOULD DISABLE  {surface.key} — 0 items stored in last {days} days"
                ))
            else:
                surface.enabled = False
                surface.save(update_fields=['enabled'])
                self.stdout.write(self.style.ERROR(
                    f"  DISABLED  {surface.key} — 0 items stored in last {days} days"
                ))

        # Summary
        self.stdout.write("")
        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"DRY RUN complete: {healthy_count} healthy, "
                f"{disabled_count} would be disabled"
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Done: {healthy_count} healthy, {disabled_count} disabled"
            ))
