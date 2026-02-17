"""
Django models for culture-flexible trend crawler.

Models:
1. Region - Geographical/cultural regions
2. TrendSurface - Configurable data sources (surfaces)
3. TrendItem - Collected trend items
4. TrendItemTranslation - Translated content (deprecated, kept for migration)
5. CrawlRun - Execution audit log (observability)

Note: TranslationSettings has been removed. Use translation.models.TranslationConfig instead.

Requirements from: /tmp/t3 + /tmp/t4 + /tmp/t7 + /tmp/t8 + /tmp/t9
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Region(models.Model):
    """
    Geographical/cultural region.

    Represents a distinct cultural/geographical area with its own
    trend ecosystem and language preferences.

    Examples:
    - us: United States (en-US)
    - jp: Japan (ja-JP)
    - kr: South Korea (ko-KR)
    - cn: China (zh-Hans)
    """

    key = models.CharField(
        max_length=10,
        unique=True,
        help_text="Unique region identifier (e.g., 'us', 'jp', 'kr')"
    )
    name = models.CharField(
        max_length=100,
        help_text="Human-readable region name (e.g., 'United States')"
    )
    default_locale = models.CharField(
        max_length=10,
        help_text="Default locale for this region (e.g., 'en-US', 'ja-JP')"
    )
    enabled = models.BooleanField(
        default=True,
        help_text="Whether this region is active"
    )

    class Meta:
        ordering = ['key']
        verbose_name = "Region"
        verbose_name_plural = "Regions"

    def __str__(self):
        return f"{self.name} ({self.key})"


class TrendSurface(models.Model):
    """
    Configurable data source (surface) for trend collection.

    Each surface represents a specific trend indicator within a region.
    Surfaces are assigned to buckets for diversity enforcement.

    Product constraint (from /tmp/t9):
    This is a candidate generator for addictive feeds, not a generic scraper.
    Buckets ensure no single content type dominates the feed.
    """

    SURFACE_TYPE_CHOICES = [
        ('ranking', 'Ranking Surface'),      # Curated/ranked lists
        ('sampler', 'Feed Sampler'),         # Algorithmic feed samples
        ('search', 'Search Trends'),         # Search spike/rank pages
        ('news', 'News Top Stories'),        # News portal rankings
        ('rss_feed', 'RSS Feed'),            # Generic RSS feeds (Feature B)
    ]

    # Bucket choices for feed diversity (from /tmp/t9)
    BUCKET_CHOICES = [
        ('hot_now', 'Hot Now'),                          # Major trending content
        ('rising', 'Rising'),                            # New gaining traction
        ('news', 'News'),                                # General news content (Feature B)
        ('category_tech', 'Tech'),                       # Technology, gadgets
        ('category_sports', 'Sports'),                   # Sports, games
        ('category_entertainment', 'Entertainment'),     # Movies, TV, music
        ('category_finance', 'Finance'),                 # Business, stocks
        ('category_gaming', 'Gaming'),                   # Video games, esports
        ('category_lifestyle', 'Lifestyle'),             # Health, food, fashion
        ('category_science', 'Science'),                 # Research, discoveries
        ('category_politics', 'Politics'),               # Government, policy
        ('region_local', 'Region Local'),                # Local mainstream portals
        ('evergreen', 'Evergreen'),                      # Slower high-quality sources
    ]

    region = models.ForeignKey(
        Region,
        on_delete=models.CASCADE,
        help_text="Region this surface belongs to"
    )
    key = models.CharField(
        max_length=100,
        help_text="Unique identifier within region (e.g., 'reddit_hot')"
    )
    platform = models.CharField(
        max_length=50,
        help_text="Platform name (e.g., 'reddit', 'youtube')"
    )
    surface_type = models.CharField(
        max_length=20,
        choices=SURFACE_TYPE_CHOICES,
        help_text="Type of trend surface"
    )
    bucket = models.CharField(
        max_length=50,
        choices=BUCKET_CHOICES,
        default='hot_now',
        help_text="Diversity bucket assignment (product constraint: no bucket > 40%)"
    )
    bucket_weight = models.FloatField(
        default=1.0,
        help_text="Weight for bucket balancing (higher = more priority). From /tmp/t9"
    )
    entrypoint = models.CharField(
        max_length=200,
        help_text="Python import path to collector (e.g., 'crawler_surfaces.reddit_hot:collect')"
    )
    enabled = models.BooleanField(
        default=True,
        help_text="Whether this surface is active"
    )
    poll_interval_seconds = models.IntegerField(
        default=3600,
        help_text="How often to collect from this surface (in seconds)"
    )
    max_items_per_run = models.IntegerField(
        default=200,
        help_text="Maximum items to collect per execution"
    )
    config_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Surface-specific configuration passed to collector"
    )
    last_cursor = models.TextField(
        null=True,
        blank=True,
        help_text="Pagination cursor from last run"
    )

    # Health tracking fields (from /tmp/t8)
    last_run_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this surface last executed (from /tmp/t8)"
    )
    last_success_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this surface last succeeded (from /tmp/t8)"
    )
    last_error = models.TextField(
        null=True,
        blank=True,
        help_text="Last error message (from /tmp/t8)"
    )

    # Legacy scheduling fields (deprecated, use health fields above)
    next_run_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When to run next (deprecated, calculated from poll_interval)"
    )

    class Meta:
        unique_together = [['region', 'key']]
        ordering = ['region', 'key']
        verbose_name = "Trend Surface"
        verbose_name_plural = "Trend Surfaces"
        indexes = [
            models.Index(fields=['enabled', 'next_run_at']),
            models.Index(fields=['region', 'bucket']),
        ]

    def __str__(self):
        return f"{self.region.key}/{self.key}"

    def is_due(self):
        """Check if this surface is due for collection."""
        if not self.enabled:
            return False
        if not self.next_run_at:
            return True
        return timezone.now() >= self.next_run_at


class TrendItem(models.Model):
    """
    Collected trend item (candidate for feed).

    Product constraint (from /tmp/t9):
    - This is a CANDIDATE for feed ranking, not final content
    - rank_position is MORE important than timestamps
    - engagement_signals and raw_payload must always be captured
    - Translation happens async, NEVER blocks collection
    """

    region = models.ForeignKey(
        Region,
        on_delete=models.CASCADE,
        help_text="Region this item belongs to"
    )
    surface = models.ForeignKey(
        TrendSurface,
        on_delete=models.CASCADE,
        help_text="Surface that collected this item"
    )
    external_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Platform's unique ID (for reference)"
    )
    canonical_hash = models.CharField(
        max_length=64,
        db_index=True,
        help_text="SHA256 hash for deduplication (hash of normalized title + URL)"
    )

    # Content
    title_original = models.TextField(
        help_text="Original title in source language"
    )
    description_original = models.TextField(
        null=True,
        blank=True,
        help_text="Original description/snippet (if available)"
    )
    original_locale = models.CharField(
        max_length=10,
        help_text="Locale of original content (e.g., 'ja-JP')"
    )
    url = models.TextField(
        help_text="Link to original content"
    )

    # Ranking & Engagement (CRITICAL from /tmp/t9)
    rank_position = models.IntegerField(
        null=True,
        blank=True,
        help_text="Position in ranking (1=top). MORE important than timestamps! (from /tmp/t9)"
    )
    engagement_signals = models.JSONField(
        default=dict,
        blank=True,
        help_text="Platform engagement data (upvotes, views, likes, etc.). Required! (from /tmp/t9)"
    )
    bucket = models.CharField(
        max_length=50,
        help_text="Bucket copied from surface at collection time (for diversity tracking)"
    )

    # Timestamps
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When content was published (less important than rank_position)"
    )
    collected_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When we collected this item"
    )

    # Raw data (CRITICAL from /tmp/t9)
    raw_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Complete platform response. NEVER throw away data! (from /tmp/t9)"
    )

    # ==========================================================================
    # Canonical translations (en-US for ranking/analysis)
    # These fields store the English translation used for cross-regional analysis
    # ==========================================================================
    canonical_title = models.TextField(
        null=True,
        blank=True,
        help_text="Canonical (en-US) title for ranking/analysis"
    )
    canonical_description = models.TextField(
        null=True,
        blank=True,
        help_text="Canonical (en-US) description for ranking/analysis"
    )
    canonical_status = models.CharField(
        max_length=20,
        default='pending',
        choices=[
            ('pending', 'Pending'),
            ('complete', 'Complete'),
            ('failed', 'Failed'),
            ('skipped', 'Skipped (already English)'),
        ],
        help_text="Canonical translation status"
    )
    canonical_engine = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        help_text="Engine used for canonical translation (deepl, openai, local, none)"
    )
    canonical_error = models.TextField(
        null=True,
        blank=True,
        help_text="Error message if canonical translation failed"
    )

    # ==========================================================================
    # Display translations (per-locale for UI)
    # zh-Hans fields for Chinese Simplified display
    # Add more locales as needed using the same pattern
    # ==========================================================================
    display_title_zh_hans = models.TextField(
        null=True,
        blank=True,
        help_text="Chinese Simplified (zh-Hans) title for display"
    )
    display_description_zh_hans = models.TextField(
        null=True,
        blank=True,
        help_text="Chinese Simplified (zh-Hans) description for display"
    )
    display_status_zh_hans = models.CharField(
        max_length=20,
        default='pending',
        choices=[
            ('pending', 'Pending'),
            ('complete', 'Complete'),
            ('failed', 'Failed'),
            ('skipped', 'Skipped (already native)'),
        ],
        help_text="zh-Hans translation status"
    )
    display_engine_zh_hans = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        help_text="Engine used for zh-Hans translation"
    )
    display_error_zh_hans = models.TextField(
        null=True,
        blank=True,
        help_text="Error message for zh-Hans display translation"
    )

    class Meta:
        ordering = ['-collected_at']
        verbose_name = "Trend Item"
        verbose_name_plural = "Trend Items"
        indexes = [
            models.Index(fields=['region', '-collected_at']),
            models.Index(fields=['canonical_hash']),
            models.Index(fields=['bucket', '-collected_at']),
            models.Index(fields=['rank_position']),
            models.Index(fields=['surface', '-collected_at']),
        ]

    def __str__(self):
        title_snippet = self.title_original[:50]
        if len(self.title_original) > 50:
            title_snippet += "..."
        return f"{title_snippet} ({self.region.key})"


class TrendItemTranslation(models.Model):
    """
    Translated version of a trend item.

    DEPRECATED: This model is deprecated. Translation data is now stored
    directly on TrendItem using the canonical_* and display_*_<locale> fields.

    This model is kept for backward compatibility during migration.
    New translations should use the TrendItem fields directly via
    translation.manager.TranslationManager.

    Original description:
    Product constraint (from /tmp/t9):
    - Translation happens ASYNC, never blocks collection
    - Canonical locale (en-US) always attempted for analysis
    - Feed can show original content, upgrade to translated later
    """

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('complete', 'Complete'),
        ('failed', 'Failed'),
    ]

    PROVIDER_CHOICES = [
        ('deepl', 'DeepL'),
        ('openai', 'OpenAI'),
        ('argostranslate', 'Argostranslate (Offline)'),
        ('none', 'No Translation (English variants)'),
    ]

    item = models.ForeignKey(
        TrendItem,
        on_delete=models.CASCADE,
        related_name='translations',
        help_text="Item this translation belongs to"
    )
    locale = models.CharField(
        max_length=10,
        help_text="Target locale (e.g., 'en-US', 'ja-JP')"
    )
    title = models.TextField(
        help_text="Translated title"
    )
    description = models.TextField(
        null=True,
        blank=True,
        help_text="Translated description (if original had one)"
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        help_text="Translation workflow state"
    )
    provider = models.CharField(
        max_length=20,
        choices=PROVIDER_CHOICES,
        null=True,
        blank=True,
        help_text="Which translation API was used"
    )
    error_message = models.TextField(
        null=True,
        blank=True,
        help_text="Error details if translation failed"
    )
    translated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When translation completed"
    )

    class Meta:
        unique_together = [['item', 'locale']]
        ordering = ['item', 'locale']
        verbose_name = "Translation"
        verbose_name_plural = "Translations"
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['locale', 'status']),
        ]

    def __str__(self):
        return f"{self.item.id} → {self.locale} ({self.status})"


class CrawlRun(models.Model):
    """
    Audit record for every surface execution.

    Observability requirement (from /tmp/t7 + /tmp/t8):
    - Provides complete verifiability of what happened
    - Tracks metrics: fetched, stored, deduped
    - Enables debugging and monitoring
    - Answers: "Is it working? What went wrong?"
    """

    STATUS_CHOICES = [
        ('success', 'Success'),
        ('failed', 'Failed'),
    ]

    surface = models.ForeignKey(
        TrendSurface,
        on_delete=models.CASCADE,
        help_text="Surface that was executed"
    )

    # Timestamps
    started_at = models.DateTimeField(
        help_text="When execution started"
    )
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When execution finished (null if still running)"
    )

    # Status
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        help_text="Success or failure status"
    )

    # Metrics (VERIFIABILITY - from /tmp/t7)
    fetched_count = models.IntegerField(
        default=0,
        help_text="Items returned by collector API"
    )
    stored_new_count = models.IntegerField(
        default=0,
        help_text="New items inserted to database"
    )
    deduped_count = models.IntegerField(
        default=0,
        help_text="Duplicate items skipped"
    )

    # Diagnostics
    error_message = models.TextField(
        null=True,
        blank=True,
        help_text="Error details if execution failed"
    )
    duration_ms = models.IntegerField(
        null=True,
        blank=True,
        help_text="Execution time in milliseconds"
    )

    # Audit
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When this record was created"
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Crawl Run"
        verbose_name_plural = "Crawl Runs"
        indexes = [
            models.Index(fields=['surface', '-created_at']),
            models.Index(fields=['status', '-created_at']),
        ]

    def __str__(self):
        status_icon = "✅" if self.status == 'success' else "❌"
        return f"{status_icon} Run #{self.id}: {self.surface.key} ({self.created_at.strftime('%Y-%m-%d %H:%M')})"

    @property
    def success_rate(self):
        """Calculate deduplication rate."""
        if self.fetched_count == 0:
            return 0
        return (self.stored_new_count / self.fetched_count) * 100

    @property
    def dedup_rate(self):
        """Calculate deduplication rate."""
        if self.fetched_count == 0:
            return 0
        return (self.deduped_count / self.fetched_count) * 100
