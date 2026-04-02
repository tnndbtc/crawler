"""
Django Admin configuration for translation engine.

Provides admin interfaces for:
- TranslationConfig: Global translation settings (singleton) with prompts
- LLMModelConfig: LLM model configurations (legacy)
- ProviderHealth: Provider health status with reset actions
"""

import asyncio
import logging

from django.contrib import admin
from django.contrib import messages
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils import timezone

from .models import TranslationConfig, ProviderHealth

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
            'fields': ('canonical_engine',),
            'description': 'Engine for canonical (en-US) translations used in ranking/analysis.'
        }),
        ('Display Translation', {
            'fields': ('display_engine',),
            'description': 'Engine for display translations (human-readable UI).'
        }),
        ('Fallback & Production', {
            'fields': ('fallback_order', 'production_mode'),
            'description': 'Fallback order is used when primary engine fails.'
        }),
        ('Locale Settings', {
            'fields': ('canonical_locale',),
            'description': (
                'Canonical locale is locked to en-US for cross-regional analysis. '
                'Target locales for display translation are configured in System Settings '
                '(translation_target_locales).'
            )
        }),
        ('Rate Limiting', {
            'fields': ('batch_size',),
        }),
        ('Translation Prompts — Single Text', {
            'fields': ('canonical_prompt', 'display_prompt'),
            'classes': ('collapse',),
            'description': (
                'Prompts used when translating a title-only item (no description). '
                'Available variables: {source_locale}, {target_locale}, {platform}, {text}'
            ),
        }),
        ('Translation Prompts — Batch (Title + Description)', {
            'fields': ('canonical_batch_prompt', 'display_batch_prompt'),
            'classes': ('collapse',),
            'description': (
                'Prompts used when translating both title and description in one call. '
                'Must instruct Claude to return JSON: {"title": "...", "description": "..."}. '
                'Available variables: {source_locale}, {target_locale}, {platform}, {title}, {description}'
            ),
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
