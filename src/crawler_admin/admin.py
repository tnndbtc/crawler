"""
Django Admin configuration for crawler_admin.

Requirements from REQUIREMENTS-MASTER.md:
- CrawlRun: Read-only with filters (status, surface, region, date)
- TrendItem: Custom filter for missing canonical translation
- TrendSurface: Health indicators, bulk enable/disable
- Region: Basic CRUD
- ItemDerivation (Translated Items): Translation results

Note: TranslationSettings has been removed. Use translation.models.TranslationConfig instead.
"""

from django.contrib import admin
from django.db.models import Q, Count, Exists, OuterRef
from django.utils.html import format_html, mark_safe
from django.utils import timezone
from .models import (
    Region,
    TrendSurface,
    TrendItem,
    CrawlRun,
    ItemDerivation,
    SystemSettings
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
        has_en_translation = ItemDerivation.objects.filter(
            item=OuterRef('pk'),
            derivation_type='translation',
            target_locale='en',
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


class AllocationConfigFilter(admin.SimpleListFilter):
    """Filter surfaces by floor/cap allocation configuration."""
    title = 'Allocation Config'
    parameter_name = 'allocation'

    def lookups(self, request, model_admin):
        return (
            ('has_floor', 'Has floor (floor_percent > 0)'),
            ('has_cap',   'Has cap (cap_percent set)'),
            ('default',   'All defaults (no floor, no cap)'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'has_floor':
            return queryset.filter(floor_percent__gt=0)
        if self.value() == 'has_cap':
            return queryset.filter(cap_percent__isnull=False)
        if self.value() == 'default':
            return queryset.filter(floor_percent=0, cap_percent__isnull=True)
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
            ('disabled', 'Disabled'),
        )

    def queryset(self, request, queryset):
        now = timezone.now()
        one_hour_ago = now - timezone.timedelta(hours=1)

        if self.value() == 'healthy':
            return queryset.filter(
                enabled=True,
                last_success_at__isnull=False,
                last_error__isnull=True
            )
        elif self.value() == 'failing':
            return queryset.filter(
                enabled=True,
                last_error__isnull=False
            )
        elif self.value() == 'stale':
            return queryset.filter(
                enabled=True,
            ).filter(
                Q(last_run_at__isnull=True) | Q(last_run_at__lt=one_hour_ago)
            )
        elif self.value() == 'disabled':
            return queryset.filter(enabled=False)
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
        'allocation_summary',
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
        AllocationConfigFilter,
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
        ('Story Selection', {
            'fields': ('selection_weight',),
            'description': (
                'Story engine weight for this surface (1.0 = normal). '
                'Overridden by story_mix.json surface_weight_overrides if that key is set. '
                'Use values 0.3–3.0 (outside this range is clamped by the selector).'
            )
        }),
        ('Selection Allocation', {
            'fields': ('floor_percent', 'cap_percent'),
            'description': (
                'Percentages of this surface\'s lang_group top-N% pool size (rolling 24h window). '
                'floor_percent: guaranteed minimum — bypasses hotness cutoff (0 = no floor). '
                'cap_percent: maximum across floor + competition combined (None = uncapped). '
                'Example: pool=20, floor=10%, cap=30% → at least 2, at most 6 items per cycle.'
            )
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
        if not obj.enabled:
            return format_html('⛔ <span style="color: gray;">Disabled</span>')
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

    def allocation_summary(self, obj):
        """Display floor/cap allocation config as a compact string."""
        parts = []
        if obj.floor_percent > 0:
            parts.append(f'floor={obj.floor_percent}%')
        if obj.cap_percent is not None:
            parts.append(f'cap={obj.cap_percent}%')
        return ' / '.join(parts) if parts else '—'
    allocation_summary.short_description = 'Allocation'

    def changelist_view(self, request, extra_context=None):
        """Add lang group pool size banner above the surface list."""
        extra_context = extra_context or {}
        extra_context['pool_size_banner'] = self._build_pool_size_banner()
        return super().changelist_view(request, extra_context=extra_context)

    def _build_pool_size_banner(self):
        """Build HTML banner showing pool sizes for all active lang groups."""
        from shared.translation_selection import (
            get_translation_settings,
            get_lang_group_pool_size,
        )

        settings = get_translation_settings()
        global_pct = settings['hot_percent']
        locale_pcts = settings.get('hot_percent_by_locale', {})

        # Get all lang_groups that have items in the 24h window
        since = timezone.now() - timezone.timedelta(hours=24)
        lang_groups = (
            TrendItem.objects
            .filter(collected_at__gte=since, lang_group__isnull=False)
            .values_list('lang_group', flat=True)
            .distinct()
            .order_by('lang_group')
        )

        rows = []
        for lg in lang_groups:
            pct = locale_pcts.get(lg, global_pct)
            pool = get_lang_group_pool_size(lg, pct)
            total = TrendItem.objects.filter(
                lang_group=lg, collected_at__gte=since,
            ).count()
            rows.append(
                f'<span style="display:inline-block;margin:2px 12px 2px 0;">'
                f'<strong>{lg}</strong>: {pool} hot / {total} total (top {pct}%)'
                f'</span>'
            )

        if not rows:
            return mark_safe(
                '<div style="background:#f0f0f0;padding:10px 14px;margin-bottom:10px;'
                'border-radius:4px;color:#666;">'
                'No items in the 24h window yet — pool sizes will appear after crawling.'
                '</div>'
            )

        return mark_safe(
            '<div style="background:#e8f4fd;padding:10px 14px;margin-bottom:10px;'
            'border-radius:4px;border:1px solid #b8daff;">'
            '<div style="font-weight:bold;margin-bottom:6px;">'
            '📊 Lang Group Pool Sizes (24h window)</div>'
            + ''.join(rows)
            + '</div>'
        )

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
        'lang_group',
        'hotness_display',
        'canonical_status',
        'rank_position',
        'collected_at'
    ]
    list_filter = [
        'region',
        'bucket',
        'base_lang',
        'lang_group',
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
        ('Language (Language-Aware System)', {
            'fields': ('base_lang', 'locale', 'lang_group', 'lang_detected_at'),
            'description': 'Language classification for selective translation'
        }),
        ('Engagement & Ranking', {
            'fields': ('rank_position', 'engagement_signals', 'published_at'),
            'description': 'rank_position is MORE important than timestamps (product constraint)'
        }),
        ('Hotness Score (Selective Translation)', {
            'fields': ('hotness', 'hotness_computed_at'),
            'description': 'Computed hotness score for translation selection (recency × engagement)'
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
            'description': 'Chinese Simplified translation for UI display (legacy inline fields)',
            'classes': ('collapse',)
        }),
        ('Topic Classification', {
            'fields': ('topic_tags', 'topic_classified_at'),
            'description': (
                'Topic labels set by topic_classifier_worker. '
                'Vocabulary: politics, finance, ai, tech, science, entertainment, '
                'sports, business, crime, society, health, environment. '
                'Empty = not yet classified or unclassifiable by heuristic.'
            ),
        }),
        ('Metadata', {
            'fields': ('canonical_hash', 'collected_at', 'raw_payload'),
            'classes': ('collapse',)
        }),
    )

    readonly_fields = [
        'canonical_hash', 'collected_at',
        'canonical_status', 'canonical_engine', 'canonical_error',
        'display_status_zh_hans', 'display_engine_zh_hans',
        'base_lang', 'locale', 'lang_group', 'lang_detected_at',
        'hotness', 'hotness_computed_at',
        'topic_classified_at',
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

    def hotness_display(self, obj):
        """Display hotness score with color coding."""
        if obj.hotness is None:
            return format_html('<span style="color: gray;">-</span>')

        hotness = obj.hotness
        if hotness >= 500:
            color = 'darkred'
            icon = '🔥🔥🔥'
        elif hotness >= 300:
            color = 'red'
            icon = '🔥🔥'
        elif hotness >= 200:
            color = 'orange'
            icon = '🔥'
        elif hotness >= 100:
            color = 'darkgoldenrod'
            icon = '⚡'
        elif hotness >= 50:
            color = 'gray'
            icon = '•'
        else:
            color = 'lightgray'
            icon = '○'

        # Format the number first, then pass as string to avoid SafeString issues
        hotness_str = f'{hotness:.1f}'
        return format_html(
            '{} <span style="color: {};">{}</span>',
            icon, color, hotness_str
        )
    hotness_display.short_description = 'Hotness'


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


@admin.register(ItemDerivation)
class ItemDerivationAdmin(admin.ModelAdmin):
    """
    Admin for item derivations (translations, summaries, etc.)

    Shows all derived content for trend items in the language-aware system.
    Derivations are stored separately from original content to maintain immutability.
    """
    list_display = [
        'derivation_id',
        'item_title_snippet',
        'derivation_type',
        'target_locale',
        'status',
        'engine',
        'created_at'
    ]
    list_filter = [
        'derivation_type',
        'target_locale',
        'status',
        'engine',
        'created_at',
    ]
    search_fields = [
        'item__title_original',
        'content_title',
        'error_message'
    ]
    ordering = ['-created_at']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Derivation Info', {
            'fields': ('item', 'derivation_type', 'target_locale')
        }),
        ('Content', {
            'fields': ('content_title', 'content_body'),
            'description': 'Derived/translated content'
        }),
        ('Processing', {
            'fields': ('engine', 'status', 'error_message'),
            'description': 'Processing status and metadata'
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    readonly_fields = ['created_at', 'updated_at']

    def item_title_snippet(self, obj):
        """Display truncated original title."""
        title = obj.item.title_original
        if len(title) > 50:
            return title[:50] + '...'
        return title
    item_title_snippet.short_description = 'Original Title'

    def has_add_permission(self, request):
        """Derivations are created by workers, not manually."""
        return False


@admin.register(SystemSettings)
class SystemSettingsAdmin(admin.ModelAdmin):
    """
    Admin for system-wide settings.

    Allows admins to configure translation behavior without code deployments.
    Settings are read by workers at runtime.
    """
    list_display = [
        'key',
        'value_display',
        'value_type',
        'updated_at',
        'updated_by'
    ]
    list_filter = [
        'value_type',
        'updated_at',
    ]
    search_fields = ['key', 'description']
    ordering = ['key']

    fieldsets = (
        ('Setting', {
            'fields': ('key', 'value_json', 'value_type'),
            'description': 'Setting key and value'
        }),
        ('Documentation', {
            'fields': ('description',),
            'description': 'Human-readable description'
        }),
        ('Audit', {
            'fields': ('updated_at', 'updated_by'),
            'classes': ('collapse',)
        }),
    )

    readonly_fields = ['updated_at']

    def get_form(self, request, obj=None, **kwargs):
        """Customize form labels based on the setting key."""
        form = super().get_form(request, obj, **kwargs)

        # Show "Percent(%)" label only for translation_hot_percent
        if obj is not None and obj.key == 'translation_hot_percent':
            form.base_fields['value_json'].label = 'Percent (%)'
            form.base_fields['value_json'].help_text = (
                'Percentage value: top X% of items by hotness to translate '
                '(e.g., 10 = top 10%, 0.001 = top 0.001%)'
            )

        return form

    def value_display(self, obj):
        """Display value with truncation for long values."""
        value_str = str(obj.value_json)
        if len(value_str) > 80:
            return value_str[:80] + '...'
        return value_str
    value_display.short_description = 'Value'

    def save_model(self, request, obj, form, change):
        """Track who updated the setting."""
        obj.updated_by = request.user.username
        super().save_model(request, obj, form, change)


# Customize admin site
admin.site.site_header = "Trend Crawler Administration"
admin.site.site_title = "Trend Crawler Admin"
admin.site.index_title = "Culture-Flexible Trend Crawler"
