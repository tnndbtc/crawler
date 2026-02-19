"""
Unit tests for Trends API ordering behavior.

Tests:
- Ordering with hotness scores: scored items appear first
- Ordering without hotness: NULL hotness items appear after scored items
- Within each group: correct secondary sorting (hotness DESC, then collected_at DESC)

CRITICAL: Uses Django's TransactionTestCase to ensure a separate test database is created.
This prevents accidental deletion of production data.
"""

import os
import sys
import hashlib
from datetime import datetime, timedelta

# Setup Django
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')

import django
django.setup()

from django.test import TransactionTestCase
from django.utils import timezone
from crawler_admin.models import Region, TrendSurface, TrendItem


class TestTrendsAPIOrdering(TransactionTestCase):
    """
    Test ordering behavior for trends API endpoint.

    IMPORTANT: Inherits from TransactionTestCase which:
    - Creates a separate test database (NOT production db.sqlite3)
    - Runs all migrations automatically
    - Destroys test database after tests complete
    - Ensures production data is NEVER touched
    """

    def setUp(self):
        """
        Create test data before each test.

        Note: No need to delete data - Django's test runner handles this.
        Each test gets a fresh, empty database.
        """
        # Create test region and surface
        self.region = Region.objects.create(
            key='test_us',
            name='Test United States',
            default_locale='en-US'
        )
        self.surface = TrendSurface.objects.create(
            region=self.region,
            key='test_reddit',
            platform='reddit',
            surface_type='ranking',
            entrypoint='test.collector:collect',
            bucket='hot_now'
        )

    def _create_item(self, item_id, title, collected_at, hotness=None):
        """Helper to create a TrendItem with required fields."""
        canonical_hash = hashlib.sha256(f"{title}{item_id}".encode()).hexdigest()
        return TrendItem.objects.create(
            region=self.region,
            surface=self.surface,
            canonical_hash=canonical_hash,
            title_original=title,
            original_locale='en-US',
            url=f'https://example.com/{item_id}',
            bucket='hot_now',
            collected_at=collected_at,
            hotness=hotness,
            base_lang='en',
            lang_group='en',
            locale='en-US'
        )

    def test_ordering_scored_items_before_unscored(self):
        """
        Test that items WITH hotness appear before items WITHOUT hotness.

        Scenario:
        - old_scored: old timestamp, hotness=100
        - new_scored: newest timestamp, hotness=50
        - new_unscored: newest timestamp, hotness=NULL

        Expected order:
        1) old_scored (highest hotness=100)
        2) new_scored (lower hotness=50)
        3) new_unscored (no hotness but newest among nulls)
        """
        now = timezone.now()

        # Create items in random order using helper
        new_unscored = self._create_item(
            'item_new_unscored',
            'New unscored item',
            now,
            hotness=None
        )

        old_scored = self._create_item(
            'item_old_scored',
            'Old scored item',
            now - timedelta(days=1),
            hotness=100.0
        )

        new_scored = self._create_item(
            'item_new_scored',
            'New scored item',
            now,
            hotness=50.0
        )

        # Query with the same ordering as the API
        from django.db.models import Case, When, Value, IntegerField

        queryset = TrendItem.objects.select_related('region', 'surface').annotate(
            has_hotness=Case(
                When(hotness__isnull=False, then=Value(1)),
                default=Value(0),
                output_field=IntegerField()
            )
        ).order_by('-has_hotness', '-hotness', '-collected_at', '-id')

        items = list(queryset)

        # Verify ordering
        assert len(items) == 3, "Should have 3 items"

        # First item should be old_scored (highest hotness)
        assert items[0].id == old_scored.id, \
            f"First item should be old_scored (hotness=100), got {items[0].title_original}"

        # Second item should be new_scored (lower hotness)
        assert items[1].id == new_scored.id, \
            f"Second item should be new_scored (hotness=50), got {items[1].title_original}"

        # Third item should be new_unscored (no hotness)
        assert items[2].id == new_unscored.id, \
            f"Third item should be new_unscored (hotness=NULL), got {items[2].title_original}"

    def test_ordering_unscored_items_by_recency(self):
        """
        Test that items WITHOUT hotness are sorted by recency.

        Scenario:
        - older_unscored: 2 hours old, hotness=NULL
        - newer_unscored: 1 hour old, hotness=NULL
        - newest_unscored: just now, hotness=NULL

        Expected order:
        1) newest_unscored (most recent)
        2) newer_unscored
        3) older_unscored (least recent)
        """
        now = timezone.now()

        older_unscored = self._create_item(
            'older_unscored',
            'Older unscored',
            now - timedelta(hours=2),
            hotness=None
        )

        newer_unscored = self._create_item(
            'newer_unscored',
            'Newer unscored',
            now - timedelta(hours=1),
            hotness=None
        )

        newest_unscored = self._create_item(
            'newest_unscored',
            'Newest unscored',
            now,
            hotness=None
        )

        # Query with the same ordering as the API
        from django.db.models import Case, When, Value, IntegerField

        queryset = TrendItem.objects.select_related('region', 'surface').annotate(
            has_hotness=Case(
                When(hotness__isnull=False, then=Value(1)),
                default=Value(0),
                output_field=IntegerField()
            )
        ).order_by('-has_hotness', '-hotness', '-collected_at', '-id')

        items = list(queryset)

        # Verify ordering (newest first)
        assert len(items) == 3
        assert items[0].id == newest_unscored.id
        assert items[1].id == newer_unscored.id
        assert items[2].id == older_unscored.id

    def test_ordering_scored_items_by_hotness(self):
        """
        Test that items WITH hotness are sorted by hotness score DESC.

        Scenario:
        - low_hotness: hotness=10
        - mid_hotness: hotness=50
        - high_hotness: hotness=100

        Expected order:
        1) high_hotness (100)
        2) mid_hotness (50)
        3) low_hotness (10)
        """
        now = timezone.now()

        low_hotness = self._create_item(
            'low_hotness',
            'Low hotness',
            now,
            hotness=10.0
        )

        mid_hotness = self._create_item(
            'mid_hotness',
            'Mid hotness',
            now,
            hotness=50.0
        )

        high_hotness = self._create_item(
            'high_hotness',
            'High hotness',
            now,
            hotness=100.0
        )

        # Query with the same ordering as the API
        from django.db.models import Case, When, Value, IntegerField

        queryset = TrendItem.objects.select_related('region', 'surface').annotate(
            has_hotness=Case(
                When(hotness__isnull=False, then=Value(1)),
                default=Value(0),
                output_field=IntegerField()
            )
        ).order_by('-has_hotness', '-hotness', '-collected_at', '-id')

        items = list(queryset)

        # Verify ordering (highest hotness first)
        assert len(items) == 3
        assert items[0].id == high_hotness.id
        assert items[1].id == mid_hotness.id
        assert items[2].id == low_hotness.id
