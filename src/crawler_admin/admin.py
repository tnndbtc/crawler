"""
Django Admin configuration for crawler_admin.

Requirements from REQUIREMENTS-MASTER.md:
- CrawlRun: Read-only with filters (status, surface, region, date)
- TrendItem: Custom filter for missing canonical translation
- TrendSurface: Health indicators, bulk enable/disable
- Region: Basic CRUD
- TrendItemTranslation: Basic CRUD with filters

Note: TranslationSettings has been removed. Use translation.models.TranslationConfig instead.
"""

from django.contrib import admin
from django.db.models import Q, Count, Exists, OuterRef
from django.utils.html import format_html
from django.utils import timezone
from .models import (
    Region,
    TrendSurface,
    TrendItem,
    TrendItemTranslation,
    CrawlRun
)


# ============================================================================
# Custom Filters
# ============================================================================

class MissingCanonicalTranslationFilter(admin.SimpleListFilter):
    """
    Custom filter to show TrendItems missing canonical en-US translation.

    From REQUIREMENTS-MASTER.md:
    "TrendItem Admin - Custom Filter: Missing canonical en-US translation"
    """
    title = 'Canonical Translation'
    parameter_name = 'canonical_missing'

    def lookups(self, request, model_admin):
        return (
            ('yes', 'Missing en-US translation'),
            ('no', 'Has en-US translation'),
        )

    def queryset(self, request, queryset):
        # Subquery to check if en-US translation exists
        has_en_translation = TrendItemTranslation.objects.filter(
            item=OuterRef('pk'),
            locale='en-US',
            status='complete'
        )

        if self.value() == 'yes':
            # Missing en-US translation
            return queryset.filter(
                ~Exists(has_en_translation)
            )
        elif self.value() == 'no':
            # Has en-US translation
            return queryset.filter(
                Exists(has_en_translation)
            )
        return queryset


class SurfaceHealthFilter(admin.SimpleListFilter):
    """Filter surfaces by health status."""
    title = 'Health Status'
    parameter_name = 'health'

    def lookups(self, request, model_admin):
        return (
            ('healthy', 'Healthy (recent success)'),
            ('failing', 'Failing (has errors)'),
            ('stale', 'Stale (no recent runs)'),
        )

    def queryset(self, request, queryset):
        now = timezone.now()
        one_hour_ago = now - timezone.timedelta(hours=1)

        if self.value() == 'healthy':
            return queryset.filter(
                last_success_at__isnull=False,
                last_error__isnull=True
            )
        elif self.value() == 'failing':
            return queryset.filter(
                last_error__isnull=False
            )
        elif self.value() == 'stale':
            return queryset.filter(
                Q(last_run_at__isnull=True) | Q(last_run_at__lt=one_hour_ago)
            )
        return queryset


# ============================================================================
# Admin Classes
# ============================================================================

@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    """
    Basic CRUD for regions.
    """
    list_display = ['key', 'name', 'default_locale', 'enabled', 'surface_count']
    list_filter = ['enabled']
    search_fields = ['key', 'name', 'default_locale']
    ordering = ['key']

    def surface_count(self, obj):
        """Show number of surfaces in this region."""
        return obj.trendsurface_set.count()
    surface_count.short_description = 'Surfaces'


@admin.register(TrendSurface)
class TrendSurfaceAdmin(admin.ModelAdmin):
    """
    Surface admin with health indicators and bulk actions.

    From REQUIREMENTS-MASTER.md:
    - Display health indicators
    - Bulk actions: enable/disable
    - Show: last_run_at, last_success_at, last_error
    """
    list_display = [
        'key',
        'region',
        'platform',
        'bucket',
        'bucket_weight',
        'health_status',
        'last_run_display',
        'last_success_display',
        'enabled'
    ]
    list_filter = [
        'enabled',
        'region',
        'platform',
        'surface_type',
        'bucket',
        SurfaceHealthFilter,
    ]
    search_fields = ['key', 'platform', 'entrypoint']
    ordering = ['region', 'key']

    fieldsets = (
        ('Basic Information', {
            'fields': ('region', 'key', 'platform', 'surface_type')
        }),
        ('Bucket Configuration', {
            'fields': ('bucket', 'bucket_weight'),
            'description': 'Bucket assignment for feed diversity (no bucket > 40%)'
        }),
        ('Collector Settings', {
            'fields': ('entrypoint', 'enabled', 'poll_interval_seconds', 'max_items_per_run')
        }),
        ('Configuration', {
            'fields': ('config_json', 'last_cursor'),
            'classes': ('collapse',)
        }),
        ('Health Monitoring', {
            'fields': ('last_run_at', 'last_success_at', 'last_error'),
            'classes': ('collapse',),
            'description': 'Automatically updated by surface worker'
        }),
        ('Legacy Fields', {
            'fields': ('next_run_at',),
            'classes': ('collapse',),
            'description': 'Deprecated fields'
        }),
    )

    readonly_fields = ['last_run_at', 'last_success_at', 'last_error']

    actions = ['enable_surfaces', 'disable_surfaces']

    def health_status(self, obj):
        """Display health status with icon."""
        if obj.last_error:
            return format_html('❌ <span style="color: red;">Error</span>')
        elif obj.last_success_at:
            return format_html('✅ <span style="color: green;">Healthy</span>')
        elif obj.last_run_at:
            return format_html('⚠️ <span style="color: orange;">Running</span>')
        else:
            return format_html('⚪ <span style="color: gray;">Never run</span>')
    health_status.short_description = 'Health'

    def last_run_display(self, obj):
        """Display last run time."""
        if obj.last_run_at:
            return timezone.localtime(obj.last_run_at).strftime('%Y-%m-%d %H:%M')
        return '-'
    last_run_display.short_description = 'Last Run'

    def last_success_display(self, obj):
        """Display last success time."""
        if obj.last_success_at:
            return timezone.localtime(obj.last_success_at).strftime('%Y-%m-%d %H:%M')
        return '-'
    last_success_display.short_description = 'Last Success'

    @admin.action(description='Enable selected surfaces')
    def enable_surfaces(self, request, queryset):
        updated = queryset.update(enabled=True)
        self.message_user(request, f'{updated} surface(s) enabled.')

    @admin.action(description='Disable selected surfaces')
    def disable_surfaces(self, request, queryset):
        updated = queryset.update(enabled=False)
        self.message_user(request, f'{updated} surface(s) disabled.')


@admin.register(TrendItem)
class TrendItemAdmin(admin.ModelAdmin):
    """
    Trend item admin with canonical translation filter.

    From REQUIREMENTS-MASTER.md:
    - Custom Filter: "Missing canonical en-US translation"
    - Filters: region, platform, bucket, canonical_missing, date
    - Display: title, region, platform, bucket, canonical_status (✅/❌)
    """
    list_display = [
        'title_snippet',
        'region',
        'platform_display',
        'bucket',
        'canonical_status',
        'rank_position',
        'collected_at'
    ]
    list_filter = [
        'region',
        'bucket',
        MissingCanonicalTranslationFilter,
        'collected_at',
    ]
    search_fields = ['title_original', 'url', 'external_id']
    ordering = ['-collected_at']
    date_hierarchy = 'collected_at'

    fieldsets = (
        ('Content', {
            'fields': ('title_original', 'description_original', 'url', 'original_locale')
        }),
        ('Classification', {
            'fields': ('region', 'surface', 'bucket', 'external_id')
        }),
        ('Engagement & Ranking', {
            'fields': ('rank_position', 'engagement_signals', 'published_at'),
            'description': 'rank_position is MORE important than timestamps (product constraint)'
        }),
        ('Canonical Translation (en-US)', {
            'fields': (
                'canonical_title', 'canonical_description',
                'canonical_status', 'canonical_engine', 'canonical_error'
            ),
            'description': 'English translation for ranking/analysis'
        }),
        ('Display Translation (zh-Hans)', {
            'fields': (
                'display_title_zh_hans', 'display_description_zh_hans',
                'display_status_zh_hans', 'display_engine_zh_hans'
            ),
            'description': 'Chinese Simplified translation for UI display',
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('canonical_hash', 'collected_at', 'raw_payload'),
            'classes': ('collapse',)
        }),
    )

    readonly_fields = [
        'canonical_hash', 'collected_at',
        'canonical_status', 'canonical_engine', 'canonical_error',
        'display_status_zh_hans', 'display_engine_zh_hans'
    ]

    def title_snippet(self, obj):
        """Display truncated title."""
        title = obj.title_original
        if len(title) > 60:
            return title[:60] + '...'
        return title
    title_snippet.short_description = 'Title'

    def platform_display(self, obj):
        """Display platform from surface."""
        return obj.surface.platform
    platform_display.short_description = 'Platform'

    def canonical_status(self, obj):
        """Show canonical translation status using new fields."""
        # First check new fields
        if obj.canonical_status == 'complete':
            return format_html('✅ <span style="color: green;">Translated</span>')
        elif obj.canonical_status == 'skipped' or obj.original_locale.startswith('en-'):
            return format_html('🇺🇸 <span style="color: blue;">Original EN</span>')
        elif obj.canonical_status == 'failed':
            return format_html('❌ <span style="color: red;">Failed</span>')
        elif obj.canonical_status == 'pending':
            # Fallback: check old translations table for backward compatibility
            has_canonical = obj.translations.filter(
                locale='en-US',
                status='complete'
            ).exists()
            if has_canonical:
                return format_html('✅ <span style="color: green;">Translated (legacy)</span>')
            return format_html('⏳ <span style="color: orange;">Pending</span>')
        else:
            return format_html('❓ <span style="color: gray;">Unknown</span>')
    canonical_status.short_description = 'Canonical EN'


@admin.register(TrendItemTranslation)
class TrendItemTranslationAdmin(admin.ModelAdmin):
    """
    Basic CRUD for translations with filters.
    """
    list_display = [
        'item_id',
        'item_title_snippet',
        'locale',
        'status',
        'provider',
        'translated_at'
    ]
    list_filter = [
        'status',
        'provider',
        'locale',
    ]
    search_fields = ['item__title_original', 'title']
    ordering = ['-translated_at']
    date_hierarchy = 'translated_at'

    fieldsets = (
        ('Translation', {
            'fields': ('item', 'locale', 'title', 'description')
        }),
        ('Status', {
            'fields': ('status', 'provider', 'error_message', 'translated_at')
        }),
    )

    readonly_fields = ['translated_at']

    def item_title_snippet(self, obj):
        """Display truncated original title."""
        title = obj.item.title_original
        if len(title) > 40:
            return title[:40] + '...'
        return title
    item_title_snippet.short_description = 'Original Title'


@admin.register(CrawlRun)
class CrawlRunAdmin(admin.ModelAdmin):
    """
    Read-only audit log for surface executions.

    From REQUIREMENTS-MASTER.md:
    - List: surface, status (✅/❌), fetched_count, stored_new_count, deduped_count, duration_ms, created_at
    - Filters: status, surface, region, date
    - Read-only (all fields)
    """
    list_display = [
        'id',
        'status_icon',
        'surface',
        'region_display',
        'fetched_count',
        'stored_new_count',
        'deduped_count',
        'duration_display',
        'created_at'
    ]
    list_filter = [
        'status',
        'surface__region',
        'surface',
        'created_at',
    ]
    search_fields = ['surface__key', 'error_message']
    ordering = ['-created_at']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Execution', {
            'fields': ('surface', 'status', 'started_at', 'finished_at', 'duration_ms')
        }),
        ('Metrics', {
            'fields': ('fetched_count', 'stored_new_count', 'deduped_count'),
            'description': 'Verifiability: fetched vs stored vs deduped'
        }),
        ('Error Details', {
            'fields': ('error_message',),
            'classes': ('collapse',)
        }),
    )

    # All fields are read-only
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def status_icon(self, obj):
        """Display status with icon."""
        if obj.status == 'success':
            return format_html('✅ Success')
        else:
            return format_html('❌ Failed')
    status_icon.short_description = 'Status'

    def region_display(self, obj):
        """Display region from surface."""
        return obj.surface.region.key
    region_display.short_description = 'Region'

    def duration_display(self, obj):
        """Display duration in human-readable format."""
        if obj.duration_ms is None:
            return '-'

        ms = obj.duration_ms
        if ms < 1000:
            return f'{ms}ms'
        elif ms < 60000:
            return f'{ms/1000:.1f}s'
        else:
            return f'{ms/60000:.1f}m'
    duration_display.short_description = 'Duration'


# Customize admin site
admin.site.site_header = "Trend Crawler Administration"
admin.site.site_title = "Trend Crawler Admin"
admin.site.index_title = "Culture-Flexible Trend Crawler"
