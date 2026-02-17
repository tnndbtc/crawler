"""
Django Admin configuration for translation engine.

Provides admin interfaces for:
- TranslationConfig: Global translation settings (singleton) with prompts
- LLMModelConfig: LLM model configurations for OpenAI
- ProviderHealth: Provider health status with reset actions
"""

import asyncio
import logging

from django.contrib import admin
from django.contrib import messages
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils import timezone

from .models import TranslationConfig, LLMModelConfig, ProviderHealth

logger = logging.getLogger(__name__)


@admin.register(TranslationConfig)
class TranslationConfigAdmin(admin.ModelAdmin):
    """
    Singleton configuration admin for translation settings.

    Special handling:
    - Only one instance allowed (pk=1)
    - Cannot delete
    - Redirects list view to edit view
    """

    fieldsets = (
        ('Master Switch', {
            'fields': ('enable_translation',),
            'description': 'Enable or disable all translation processing'
        }),
        ('Canonical Translation', {
            'fields': ('canonical_engine', 'canonical_model'),
            'description': (
                'Engine and model for canonical (en-US) translations used in ranking/analysis. '
                'Select an LLM Model when using OpenAI engine.'
            )
        }),
        ('Display Translation', {
            'fields': ('display_engine', 'display_model'),
            'description': (
                'Engine and model for display translations (human-readable UI). '
                'Select an LLM Model when using OpenAI engine.'
            )
        }),
        ('Fallback & Production', {
            'fields': ('fallback_order', 'production_mode'),
            'description': 'Fallback order is used when primary engine fails.'
        }),
        ('Locale Settings', {
            'fields': ('canonical_locale', 'enabled_locales'),
            'description': (
                'Canonical locale is locked to en-US for cross-regional analysis. '
                'Enabled locales are additional languages for display translations.'
            )
        }),
        ('Rate Limiting', {
            'fields': ('max_chars_per_request', 'batch_size'),
        }),
    )

    readonly_fields = ['canonical_locale']

    actions = ['test_engines']

    def has_add_permission(self, request):
        """Only allow adding if no instance exists."""
        return not TranslationConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        """Cannot delete singleton."""
        return False

    def changelist_view(self, request, extra_context=None):
        """Redirect list view to edit view."""
        try:
            obj = TranslationConfig.objects.get(pk=1)
            from django.urls import reverse
            from django.shortcuts import redirect
            return redirect(
                reverse('admin:translation_translationconfig_change', args=[obj.pk])
            )
        except TranslationConfig.DoesNotExist:
            pass

        return super().changelist_view(request, extra_context)

    @admin.action(description='Test translation engines')
    def test_engines(self, request, queryset):
        """Test all configured translation engines."""
        from .manager import TranslationManager

        try:
            manager = TranslationManager()

            # Run async test in sync context
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                results = loop.run_until_complete(manager.test_all_engines())
            finally:
                loop.close()

            # Report results
            for engine_name, success in results.items():
                if success:
                    self.message_user(
                        request,
                        f"✅ Engine '{engine_name}' is working",
                        messages.SUCCESS
                    )
                else:
                    self.message_user(
                        request,
                        f"❌ Engine '{engine_name}' failed",
                        messages.ERROR
                    )

        except Exception as e:
            self.message_user(
                request,
                f"Error testing engines: {e}",
                messages.ERROR
            )


@admin.register(LLMModelConfig)
class LLMModelConfigAdmin(admin.ModelAdmin):
    """
    Admin for LLM model configurations.

    Each entry is self-contained with model parameters AND prompts.
    Create separate entries for different purposes (e.g., "GPT-4o-mini Canonical", "GPT-4o-mini Display").
    """

    list_display = [
        'name',
        'provider',
        'model_id',
        'temperature',
        'is_default_display',
        'enabled_display',
    ]
    list_filter = ['provider', 'enabled', 'is_default']
    search_fields = ['name', 'model_id', 'description']
    ordering = ['provider', 'name']

    fieldsets = (
        ('Model', {
            'fields': ('name', 'provider', 'model_id'),
            'description': 'Create separate entries for different purposes (e.g., "GPT-4o-mini Canonical", "GPT-4o-mini Display")'
        }),
        ('Parameters', {
            'fields': ('temperature', 'top_p', 'max_tokens'),
            'description': (
                'Temperature: 0.0-1.0 (lower = more deterministic). '
                'Top-p: 0.0-1.0 (nucleus sampling). '
                'Max tokens: Maximum response length.'
            )
        }),
        ('Prompts', {
            'fields': ('system_prompt', 'developer_prompt', 'user_prompt'),
            'description': 'Variables: {source_platform}, {original_language}, {target_locale}, {title}, {description}'
        }),
        ('Status', {
            'fields': ('enabled', 'is_default', 'description'),
        }),
    )

    actions = ['enable_models', 'disable_models', 'set_as_default']

    def enabled_display(self, obj):
        """Display enabled status with icon."""
        if obj.enabled:
            return format_html('✅ <span style="color: green;">Enabled</span>')
        return format_html('❌ <span style="color: red;">Disabled</span>')
    enabled_display.short_description = 'Status'

    def is_default_display(self, obj):
        """Display default status with icon."""
        if obj.is_default:
            return format_html('⭐ <span style="color: gold;">Default</span>')
        return '-'
    is_default_display.short_description = 'Default'

    @admin.action(description='Enable selected models')
    def enable_models(self, request, queryset):
        updated = queryset.update(enabled=True)
        self.message_user(request, f'{updated} model(s) enabled.')

    @admin.action(description='Disable selected models')
    def disable_models(self, request, queryset):
        updated = queryset.update(enabled=False)
        self.message_user(request, f'{updated} model(s) disabled.')

    @admin.action(description='Set as default (for each provider)')
    def set_as_default(self, request, queryset):
        for model in queryset:
            model.is_default = True
            model.save()
        self.message_user(
            request,
            f'Set {queryset.count()} model(s) as default. '
            f'Note: Only one default per provider is allowed.'
        )



@admin.register(ProviderHealth)
class ProviderHealthAdmin(admin.ModelAdmin):
    """
    Admin for provider health status.

    Features:
    - Read-only view of provider health states
    - Reset actions to recover providers
    - Color-coded status display
    """

    list_display = [
        'provider',
        'state_display',
        'last_success_at',
        'consecutive_failures',
        'last_state_change_at',
    ]
    list_filter = ['state']
    ordering = ['provider']

    # All fields are read-only except through actions
    readonly_fields = [
        'provider',
        'state',
        'state_display',
        'last_error_message',
        'last_error_code',
        'last_state_change_at',
        'last_success_at',
        'consecutive_failures',
    ]

    fieldsets = (
        ('Provider', {
            'fields': ('provider', 'state_display'),
        }),
        ('Health Status', {
            'fields': ('last_success_at', 'consecutive_failures', 'last_state_change_at'),
        }),
        ('Last Error', {
            'fields': ('last_error_message', 'last_error_code'),
            'classes': ('collapse',),
        }),
    )

    actions = ['reset_to_available', 'reset_all_providers']

    def has_add_permission(self, request):
        """Cannot manually add providers."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Cannot delete providers."""
        return False

    def state_display(self, obj):
        """Display state with color-coded icon."""
        state_colors = {
            'available': ('✅', 'green'),
            'unavailable_funds': ('💰', 'red'),
            'unavailable_auth': ('🔐', 'red'),
            'unavailable_rate_limit': ('⏱️', 'orange'),
            'unavailable_transient': ('⚠️', 'orange'),
        }

        icon, color = state_colors.get(obj.state, ('❓', 'gray'))
        state_label = obj.get_state_display()

        return format_html(
            '{} <span style="color: {};">{}</span>',
            icon,
            color,
            state_label
        )
    state_display.short_description = 'Status'

    @admin.action(description='Reset selected providers to Available')
    def reset_to_available(self, request, queryset):
        """Reset selected providers to available state."""
        reset_count = 0
        for health in queryset:
            health.mark_available()
            reset_count += 1
            logger.info(f"Admin reset provider {health.provider} to available")

        self.message_user(
            request,
            f"✅ Reset {reset_count} provider(s) to Available state.",
            messages.SUCCESS
        )

    @admin.action(description='Reset ALL providers (clear STOPPED state)')
    def reset_all_providers(self, request, queryset):
        """Reset ALL providers to available state, clearing STOPPED state."""
        ProviderHealth.reset_all()
        logger.info("Admin reset ALL providers to available")

        self.message_user(
            request,
            "✅ All providers reset to Available. STOPPED state cleared.",
            messages.SUCCESS
        )

    def changelist_view(self, request, extra_context=None):
        """Add system status summary to changelist view."""
        extra_context = extra_context or {}

        # Get overall system status
        providers = ProviderHealth.get_all_providers()
        available_count = sum(1 for p in providers.values() if p.is_available)
        total_count = len(providers)

        if available_count == total_count:
            system_status = '✅ All providers available'
            status_class = 'success'
        elif available_count > 0:
            system_status = f'⚠️ Degraded: {available_count}/{total_count} providers available'
            status_class = 'warning'
        else:
            system_status = '⛔ STOPPED: No providers available'
            status_class = 'error'

        extra_context['system_status'] = system_status
        extra_context['system_status_class'] = status_class
        extra_context['available_count'] = available_count
        extra_context['total_count'] = total_count

        return super().changelist_view(request, extra_context)
