"""
Django models for culture-flexible trend crawler.

Models:
1. Region - Geographical/cultural regions
2. TrendSurface - Configurable data sources (surfaces)
3. TrendItem - Collected trend items
4. TrendItemTranslation - Translated content (deprecated, kept for migration)
5. CrawlRun - Execution audit log (observability)
6. ItemDerivation - Derived content (translations, summaries, etc.)
7. SystemSettings - Admin-configurable system settings
8. DomainPolicy - Per-domain rate limiting and circuit breaker state
9. URLCache - HTTP caching metadata (ETag, Last-Modified)
10. DomainMetrics - Time-series observability data for HTTP requests

Note: TranslationSettings has been removed. Use translation.models.TranslationConfig instead.

Requirements from: /tmp/t3 + /tmp/t4 + /tmp/t7 + /tmp/t8 + /tmp/t9
Language-aware selective translation system with hotness-based selection.
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
        help_text="AI-generated summary (repurposed from raw snippet). Raw content preserved in raw_payload."
    )
    summary_status = models.CharField(
        max_length=20,
        default='pending',
        choices=[
            ('pending', 'Pending'),
            ('complete', 'Complete'),
            ('failed', 'Failed'),
            ('skipped', 'Skipped (no summarizable content)'),
        ],
        help_text="AI summarization status for description_original"
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
        help_text="Engine used for canonical translation (claude, none)"
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

    # ==========================================================================
    # Language-aware system (selective translation)
    # Added for language detection and hotness-based translation selection
    # ==========================================================================
    base_lang = models.CharField(
        max_length=10,
        null=True,
        blank=True,
        db_index=True,
        help_text="Base language code (e.g., 'zh', 'en', 'ja')"
    )
    locale = models.CharField(
        max_length=10,
        null=True,
        blank=True,
        db_index=True,
        help_text="Full locale/region tag (e.g., 'zh-Hans', 'en-US')"
    )
    lang_group = models.CharField(
        max_length=10,
        null=True,
        blank=True,
        db_index=True,
        help_text="Language grouping key for feeds/ranking (zh-Hans/zh-Hant → 'zh')"
    )

    # Hotness scoring for selective translation
    hotness = models.FloatField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Computed hotness score (recency × engagement)"
    )

    # Metadata timestamps
    lang_detected_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When language detection was performed"
    )
    hotness_computed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When hotness score was last computed"
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
            # Language-aware indexes
            models.Index(fields=['lang_group', '-hotness']),
            models.Index(fields=['base_lang', '-collected_at']),
            models.Index(fields=['hotness']),
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
        ('claude', 'Claude CLI'),
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


class ItemDerivation(models.Model):
    """
    Derived content (translations, summaries, embeddings, etc.)

    This table stores all derived/processed versions of TrendItems,
    keeping the canonical original data immutable.

    Key features:
    - Translations stored separately (never overwrite originals)
    - Supports multiple derivation types (translation, summary, embedding)
    - Unique constraint prevents duplicate derivations
    - Status tracking for async processing
    """

    derivation_id = models.AutoField(primary_key=True)
    item = models.ForeignKey(
        TrendItem,
        on_delete=models.CASCADE,
        related_name='derivations',
        help_text="Original item this derivation belongs to"
    )

    # Type and target
    derivation_type = models.CharField(
        max_length=20,
        choices=[
            ('translation', 'Translation'),
            ('summary', 'AI Summary'),
            ('embedding', 'Semantic Embedding'),
        ],
        default='translation',
        help_text="Type of derived content"
    )
    target_locale = models.CharField(
        max_length=10,
        help_text="Target locale for this derivation (e.g., 'zh-Hans')"
    )

    # Content (for translations and summaries)
    content_title = models.TextField(
        help_text="Derived title content"
    )
    content_body = models.TextField(
        null=True,
        blank=True,
        help_text="Derived body/description content"
    )

    # Metadata
    engine = models.CharField(
        max_length=20,
        help_text="Engine/provider used (e.g., 'claude')"
    )
    status = models.CharField(
        max_length=20,
        default='complete',
        choices=[
            ('pending', 'Pending'),
            ('complete', 'Complete'),
            ('failed', 'Failed'),
        ],
        help_text="Processing status"
    )
    error_message = models.TextField(
        null=True,
        blank=True,
        help_text="Error message if processing failed"
    )

    # Timestamps
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When this derivation was created"
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="When this derivation was last updated"
    )

    class Meta:
        unique_together = [['item', 'derivation_type', 'target_locale']]
        ordering = ['-created_at']
        verbose_name = "Translated Item"
        verbose_name_plural = "Translated Items"
        indexes = [
            models.Index(fields=['item', 'derivation_type', 'target_locale']),
            models.Index(fields=['derivation_type', 'status']),
            models.Index(fields=['target_locale', 'status']),
        ]

    def __str__(self):
        return f"{self.derivation_type} ({self.target_locale}) for item #{self.item_id}"


class SystemSettings(models.Model):
    """
    Admin-configurable system settings.

    Provides a database-backed configuration system for runtime settings
    that admins can modify without code deployments.

    Usage:
        # Get setting with fallback
        hot_percent = SystemSettings.get_setting('translation_hot_percent', default=10)

        # Set setting
        SystemSettings.objects.update_or_create(
            key='translation_hot_percent',
            defaults={'value_json': 15, 'description': 'Top X% to translate'}
        )
    """

    key = models.CharField(
        max_length=100,
        primary_key=True,
        help_text="Unique setting identifier"
    )
    value_json = models.JSONField(
        help_text="Setting value (can be int, float, bool, string, or JSON object)"
    )
    description = models.TextField(
        blank=True,
        help_text="Human-readable description of this setting"
    )
    value_type = models.CharField(
        max_length=20,
        choices=[
            ('integer', 'Integer'),
            ('float', 'Float'),
            ('boolean', 'Boolean'),
            ('string', 'String'),
            ('json', 'JSON Object'),
        ],
        default='integer',
        help_text="Type hint for admin UI"
    )

    # Audit
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="When this setting was last modified"
    )
    updated_by = models.CharField(
        max_length=100,
        blank=True,
        help_text="Who last modified this setting (username or system)"
    )

    class Meta:
        verbose_name = "System Setting"
        verbose_name_plural = "System Settings"
        ordering = ['key']

    def __str__(self):
        return f"{self.key} = {self.value_json}"

    @classmethod
    def get_setting(cls, key: str, default=None):
        """
        Get setting value with fallback.

        Args:
            key: Setting key
            default: Default value if key doesn't exist

        Returns:
            Setting value or default
        """
        try:
            return cls.objects.get(key=key).value_json
        except cls.DoesNotExist:
            return default

    @classmethod
    def set_setting(cls, key: str, value, description: str = '', value_type: str = 'integer', updated_by: str = 'system'):
        """
        Set or update a setting.

        Args:
            key: Setting key
            value: Setting value
            description: Optional description
            value_type: Type hint ('integer', 'float', 'boolean', 'string', 'json')
            updated_by: Who is making this change

        Returns:
            SystemSettings instance
        """
        obj, created = cls.objects.update_or_create(
            key=key,
            defaults={
                'value_json': value,
                'description': description,
                'value_type': value_type,
                'updated_by': updated_by,
            }
        )
        return obj


class DomainPolicy(models.Model):
    """
    Per-domain rate limiting and circuit breaker state.

    Mirrors the ProviderHealth pattern from translation.models.ProviderHealth
    for HTTP request resilience. Enables crawler to be a "good citizen" by:
    - Respecting per-domain rate limits (RPM)
    - Limiting concurrent connections
    - Opening circuit breaker after repeated failures
    - Adaptive backoff when receiving 429/403

    Circuit breaker states:
    - closed: Normal operation (default)
    - open: Circuit breaker tripped, blocking all requests
    - half_open: Testing recovery with single request
    """

    CIRCUIT_STATE_CHOICES = [
        ('closed', 'Closed (Normal)'),
        ('open', 'Open (Blocked)'),
        ('half_open', 'Half-Open (Testing)'),
    ]

    domain = models.CharField(
        max_length=255,
        primary_key=True,
        help_text="Domain name (e.g., 'news.ycombinator.com')"
    )

    # Rate limiting
    max_rpm = models.IntegerField(
        default=30,
        help_text="Requests per minute limit (default: 30)"
    )
    max_concurrent = models.IntegerField(
        default=5,
        help_text="Max simultaneous requests (default: 5)"
    )

    # Circuit breaker
    circuit_state = models.CharField(
        max_length=20,
        choices=CIRCUIT_STATE_CHOICES,
        default='closed',
        help_text="Current circuit breaker state"
    )
    circuit_failure_threshold = models.IntegerField(
        default=5,
        help_text="Consecutive failures before opening circuit (default: 5)"
    )
    circuit_cooldown_seconds = models.IntegerField(
        default=300,
        help_text="Cooldown duration when circuit is open (default: 300s = 5min)"
    )
    consecutive_failures = models.IntegerField(
        default=0,
        help_text="Current consecutive failure count"
    )

    # Adaptive backoff
    current_backoff_multiplier = models.FloatField(
        default=1.0,
        help_text="Current backoff multiplier (1.0 = normal, >1.0 = slowed down)"
    )
    backoff_decay_rate = models.FloatField(
        default=0.1,
        help_text="How fast backoff recovers on success (default: 0.1 = 10% per success)"
    )

    # Timestamps
    last_request_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last request timestamp (for rate limiting)"
    )
    last_success_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last successful request timestamp"
    )
    last_failure_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last failure timestamp"
    )
    circuit_opened_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When circuit was opened (for cooldown calculation)"
    )

    # Human browsing simulation fields
    simulation_config = models.JSONField(
        null=True,
        blank=True,
        help_text="Per-domain simulation configuration overrides"
    )
    simulation_penalty_until = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Adaptive backoff penalty expiration (null = no penalty active)"
    )
    simulation_delay_multiplier = models.FloatField(
        default=1.0,
        help_text="Delay multiplier during penalty (default: 1.0 = normal, 2.5 = slower)"
    )
    simulation_probability_multiplier = models.FloatField(
        default=1.0,
        help_text="Probability multiplier during penalty (default: 1.0 = normal, 0.5 = reduced)"
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Domain Policy"
        verbose_name_plural = "Domain Policies"
        ordering = ['domain']
        indexes = [
            models.Index(fields=['circuit_state']),
        ]

    def __str__(self):
        return f"{self.domain} ({self.get_circuit_state_display()})"

    @property
    def is_circuit_open(self) -> bool:
        """Check if circuit is open (blocking requests)."""
        return self.circuit_state == 'open'

    @property
    def is_cooldown_expired(self) -> bool:
        """Check if circuit cooldown period has expired."""
        if not self.circuit_opened_at:
            return True
        elapsed = (timezone.now() - self.circuit_opened_at).total_seconds()
        return elapsed >= self.circuit_cooldown_seconds

    def record_success(self):
        """Record successful request and update state."""
        self.last_success_at = timezone.now()
        self.last_request_at = timezone.now()
        self.consecutive_failures = 0

        # Decay backoff on success
        if self.current_backoff_multiplier > 1.0:
            self.current_backoff_multiplier = max(
                1.0,
                self.current_backoff_multiplier - self.backoff_decay_rate
            )

        # Transition from half_open to closed on success
        if self.circuit_state == 'half_open':
            self.circuit_state = 'closed'
            self.circuit_opened_at = None

        self.save()

    def record_failure(self, error_type: str = 'generic'):
        """
        Record failed request and potentially open circuit.

        Args:
            error_type: 'rate_limit' (429), 'forbidden' (403), 'server_error' (5xx), 'generic'
        """
        self.last_failure_at = timezone.now()
        self.last_request_at = timezone.now()
        self.consecutive_failures += 1

        # Increase backoff on rate limit or forbidden
        if error_type in ['rate_limit', 'forbidden']:
            self.current_backoff_multiplier = min(
                5.0,  # Cap at 5x slowdown
                self.current_backoff_multiplier * 1.5
            )

        # Open circuit if threshold exceeded
        if self.consecutive_failures >= self.circuit_failure_threshold:
            self.circuit_state = 'open'
            self.circuit_opened_at = timezone.now()

        self.save()

    def transition_to_half_open(self):
        """Transition from open to half_open state."""
        if self.circuit_state == 'open':
            self.circuit_state = 'half_open'
            self.save()

    @classmethod
    def get_or_create_policy(cls, domain: str) -> 'DomainPolicy':
        """Get or create policy for domain with defaults."""
        obj, _ = cls.objects.get_or_create(domain=domain)
        return obj


class URLCache(models.Model):
    """
    HTTP caching metadata for ETags and Last-Modified headers.

    Enables efficient HTTP caching by:
    - Storing ETag and Last-Modified from responses
    - Sending If-None-Match and If-Modified-Since on subsequent requests
    - Handling 304 Not Modified responses to skip re-processing

    URL normalization:
    - Query params sorted alphabetically
    - Tracking params stripped (utm_*, fbclid, etc.)
    - Hash computed from normalized URL
    """

    url_hash = models.CharField(
        max_length=64,
        primary_key=True,
        help_text="SHA256 hash of normalized URL"
    )
    url = models.TextField(
        help_text="Original URL"
    )

    # HTTP caching headers
    etag = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="ETag header value from response"
    )
    last_modified = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Last-Modified header value from response"
    )

    # Cache expiration
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Cache expiration timestamp (from Cache-Control or Expires header)"
    )

    # Metadata
    last_validated_at = models.DateTimeField(
        auto_now=True,
        help_text="Last time this cache entry was validated (touched)"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When this cache entry was created"
    )

    class Meta:
        verbose_name = "URL Cache"
        verbose_name_plural = "URL Caches"
        ordering = ['-last_validated_at']
        indexes = [
            models.Index(fields=['expires_at']),
            models.Index(fields=['-last_validated_at']),
        ]

    def __str__(self):
        return f"Cache: {self.url[:50]}..."

    @property
    def is_expired(self) -> bool:
        """Check if cache entry is expired."""
        if not self.expires_at:
            return False
        return timezone.now() >= self.expires_at

    def touch(self):
        """Update last_validated_at timestamp."""
        self.last_validated_at = timezone.now()
        self.save(update_fields=['last_validated_at'])


class DomainMetrics(models.Model):
    """
    Time-series observability data for HTTP requests.

    Aggregates metrics per domain per hour for:
    - Request counts by status code (2xx, 4xx, 5xx)
    - Latency percentiles (p50, p95)
    - Retry counts
    - Circuit breaker open duration

    Used by management command `python manage.py domain_report` for visibility.
    """

    domain = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Domain name (e.g., 'news.ycombinator.com')"
    )
    hour_bucket = models.DateTimeField(
        db_index=True,
        help_text="Hourly aggregation bucket (truncated to hour)"
    )

    # Request counts by status
    requests_2xx = models.IntegerField(
        default=0,
        help_text="Successful requests (200-299)"
    )
    requests_4xx = models.IntegerField(
        default=0,
        help_text="Client errors (400-499)"
    )
    requests_5xx = models.IntegerField(
        default=0,
        help_text="Server errors (500-599)"
    )
    requests_429 = models.IntegerField(
        default=0,
        help_text="Rate limit errors (429 Too Many Requests)"
    )
    requests_403 = models.IntegerField(
        default=0,
        help_text="Forbidden errors (403)"
    )

    # Latency metrics (milliseconds)
    latency_p50 = models.IntegerField(
        null=True,
        blank=True,
        help_text="Median latency in milliseconds"
    )
    latency_p95 = models.IntegerField(
        null=True,
        blank=True,
        help_text="95th percentile latency in milliseconds"
    )

    # Retry metrics
    retry_count = models.IntegerField(
        default=0,
        help_text="Total number of retries performed"
    )

    # Circuit breaker metrics
    circuit_open_duration_seconds = models.IntegerField(
        default=0,
        help_text="Total seconds circuit was open in this hour"
    )

    # Human browsing simulation metrics
    direct_requests = models.IntegerField(
        default=0,
        help_text="Normal crawler requests (actual content fetches)"
    )
    simulated_requests = models.IntegerField(
        default=0,
        help_text="Human-like simulation requests (homepage visits, favicon prefetch)"
    )
    simulation_fallbacks = models.IntegerField(
        default=0,
        help_text="Failed simulation attempts (errors, timeouts)"
    )
    simulation_budget_blocked = models.IntegerField(
        default=0,
        help_text="Simulation requests blocked by budget enforcement"
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [['domain', 'hour_bucket']]
        verbose_name = "Domain Metrics"
        verbose_name_plural = "Domain Metrics"
        ordering = ['-hour_bucket', 'domain']
        indexes = [
            models.Index(fields=['domain', '-hour_bucket']),
            models.Index(fields=['-hour_bucket']),
        ]

    def __str__(self):
        return f"{self.domain} @ {self.hour_bucket.strftime('%Y-%m-%d %H:00')}"

    @property
    def total_requests(self) -> int:
        """Total requests in this hour."""
        return self.requests_2xx + self.requests_4xx + self.requests_5xx

    @property
    def success_rate(self) -> float:
        """Success rate as percentage."""
        if self.total_requests == 0:
            return 0.0
        return (self.requests_2xx / self.total_requests) * 100
