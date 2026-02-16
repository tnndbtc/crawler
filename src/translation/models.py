"""
Django models for translation engine configuration.

Models:
- TranslationConfig: Global translation settings (singleton)
- LLMModelConfig: LLM model configurations for OpenAI engine
- PromptTemplate: Admin-editable prompts for LLM translations
- ProviderHealth: Track health status of translation providers
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class TranslationConfig(models.Model):
    """
    Global translation configuration (singleton).

    Replaces the old TranslationSettings model with enhanced features:
    - Separate canonical and display engine selection
    - Configurable fallback order
    - Production mode flag
    """

    ENGINE_CHOICES = [
        ('deepl', 'DeepL'),
        ('openai', 'OpenAI'),
    ]

    # Engine selection
    canonical_engine = models.CharField(
        max_length=20,
        choices=ENGINE_CHOICES,
        default='deepl',
        help_text="Engine for canonical (en-US) translations used in ranking/analysis"
    )
    display_engine = models.CharField(
        max_length=20,
        choices=ENGINE_CHOICES,
        default='deepl',
        help_text="Engine for display translations (human-readable UI)"
    )
    fallback_order = models.JSONField(
        default=list,
        blank=True,
        help_text="Ordered list of fallback engines: ['deepl', 'openai']"
    )

    # Feature flags
    enable_translation = models.BooleanField(
        default=True,
        help_text="Master switch for translation worker"
    )
    production_mode = models.BooleanField(
        default=True,
        help_text="If True, local engine is disabled (API-only mode)"
    )

    # Locale settings
    canonical_locale = models.CharField(
        max_length=10,
        default='en-US',
        help_text="Base language for trend analysis (LOCKED to en-US)"
    )
    enabled_locales = models.JSONField(
        default=list,
        blank=True,
        help_text="Additional locales to translate to (e.g., ['zh-Hans'])"
    )

    # Rate limiting
    max_chars_per_request = models.IntegerField(
        default=5000,
        help_text="Maximum characters per translation request"
    )
    batch_size = models.IntegerField(
        default=10,
        help_text="Number of items to process per worker cycle"
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Translation Configuration"
        verbose_name_plural = "Translation Configuration"

    def __str__(self):
        return f"Translation Config (canonical: {self.canonical_engine}, display: {self.display_engine})"

    def save(self, *args, **kwargs):
        """Enforce singleton pattern and validate settings."""
        # Singleton: force pk=1
        self.pk = 1

        # Validate canonical locale
        if self.canonical_locale != 'en-US':
            raise ValidationError(
                "canonical_locale must be 'en-US' for proper trend analysis"
            )

        # Set default fallback order if empty
        if not self.fallback_order:
            self.fallback_order = ['deepl', 'openai']

        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Prevent deletion of singleton."""
        raise ValidationError("Cannot delete Translation Configuration")

    @classmethod
    def get_config(cls) -> 'TranslationConfig':
        """Get or create singleton instance."""
        obj, created = cls.objects.get_or_create(pk=1)
        if created:
            # Set default fallback order
            obj.fallback_order = ['deepl', 'openai']
            obj.enabled_locales = ['zh-Hans']
            obj.save()
        return obj

    def get_effective_fallback_order(self, translation_type: str = 'display') -> list:
        """
        Get fallback engines for the given translation type.

        Args:
            translation_type: 'canonical' or 'display'

        Returns:
            List of engine names in fallback order
        """
        # Start with the primary engine
        if translation_type == 'canonical':
            primary = self.canonical_engine
        else:
            primary = self.display_engine

        # Build fallback list
        engines = [primary]
        for engine in self.fallback_order:
            if engine not in engines:
                engines.append(engine)

        return engines


class LLMModelConfig(models.Model):
    """
    LLM model configuration for OpenAI engine.

    Allows admin to configure multiple models with different settings.
    """

    PROVIDER_CHOICES = [
        ('openai', 'OpenAI'),
        ('anthropic', 'Anthropic'),
    ]

    name = models.CharField(
        max_length=50,
        unique=True,
        help_text="Human-readable name (e.g., 'GPT-4o Mini')"
    )
    provider = models.CharField(
        max_length=20,
        choices=PROVIDER_CHOICES,
        default='openai',
        help_text="LLM provider"
    )
    model_id = models.CharField(
        max_length=100,
        help_text="Model identifier (e.g., 'gpt-4o-mini', 'claude-3-haiku')"
    )
    temperature = models.FloatField(
        default=0.3,
        help_text="Sampling temperature (0.0-1.0, lower = more deterministic)"
    )
    top_p = models.FloatField(
        default=1.0,
        help_text="Top-p (nucleus) sampling (0.0-1.0, lower = more focused)"
    )
    max_tokens = models.IntegerField(
        default=1000,
        help_text="Maximum tokens in response"
    )
    enabled = models.BooleanField(
        default=True,
        help_text="Whether this model is available for use"
    )
    is_default = models.BooleanField(
        default=False,
        help_text="Use this model as default for OpenAI engine"
    )
    description = models.TextField(
        blank=True,
        help_text="Notes about this model (cost, quality, etc.)"
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "LLM Model"
        verbose_name_plural = "LLM Models"
        ordering = ['provider', 'name']

    def __str__(self):
        status = "enabled" if self.enabled else "disabled"
        default = " (default)" if self.is_default else ""
        return f"{self.name} [{self.model_id}] - {status}{default}"

    def save(self, *args, **kwargs):
        """Ensure only one default model per provider."""
        if self.is_default:
            # Clear other defaults for this provider
            LLMModelConfig.objects.filter(
                provider=self.provider,
                is_default=True
            ).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    @classmethod
    def get_default(cls, provider: str = 'openai') -> 'LLMModelConfig':
        """Get default model for provider."""
        model = cls.objects.filter(
            provider=provider,
            is_default=True,
            enabled=True
        ).first()

        if not model:
            # Fallback to any enabled model
            model = cls.objects.filter(
                provider=provider,
                enabled=True
            ).first()

        return model


class PromptTemplate(models.Model):
    """
    Admin-editable prompt templates for LLM-based translation.

    Supports variable substitution:
    - {source_platform}: Platform name (reddit, youtube, etc.)
    - {region}: Region key (us, jp, kr, etc.)
    - {original_language}: Source locale
    - {title}: Original title
    - {description}: Original description
    - {text}: Text to translate
    - {source_locale}: Source locale code
    - {target_locale}: Target locale code
    """

    TRANSLATION_TYPE_CHOICES = [
        ('canonical', 'Canonical (Semantic Normalization)'),
        ('display', 'Display (Human-Readable)'),
    ]

    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Template name for identification"
    )
    translation_type = models.CharField(
        max_length=20,
        choices=TRANSLATION_TYPE_CHOICES,
        help_text="Type of translation this template is for"
    )
    system_prompt = models.TextField(
        help_text="System role message (defines translator behavior)"
    )
    developer_prompt = models.TextField(
        blank=True,
        help_text="Developer role message (optional, for additional instructions)"
    )
    user_prompt_template = models.TextField(
        help_text="User message template with {variables}"
    )
    description = models.TextField(
        blank=True,
        help_text="Notes about when to use this template"
    )
    active = models.BooleanField(
        default=True,
        help_text="Whether this template is available for use"
    )
    is_default = models.BooleanField(
        default=False,
        help_text="Use this template as default for its translation type"
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Prompt Template"
        verbose_name_plural = "Prompt Templates"
        ordering = ['translation_type', 'name']

    def __str__(self):
        status = "active" if self.active else "inactive"
        default = " (default)" if self.is_default else ""
        return f"{self.name} [{self.translation_type}] - {status}{default}"

    def save(self, *args, **kwargs):
        """Ensure only one default template per type."""
        if self.is_default:
            # Clear other defaults for this type
            PromptTemplate.objects.filter(
                translation_type=self.translation_type,
                is_default=True
            ).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    def render(self, context: dict) -> dict:
        """
        Render prompts with context variables.

        Args:
            context: Dict with variable values

        Returns:
            Dict with rendered system, developer, and user prompts
        """
        def substitute(template: str, ctx: dict) -> str:
            result = template
            for key, value in ctx.items():
                result = result.replace(f'{{{key}}}', str(value) if value else '')
            return result

        return {
            'system': substitute(self.system_prompt, context),
            'developer': substitute(self.developer_prompt, context) if self.developer_prompt else None,
            'user': substitute(self.user_prompt_template, context),
        }

    @classmethod
    def get_default(cls, translation_type: str) -> 'PromptTemplate':
        """Get default template for translation type."""
        template = cls.objects.filter(
            translation_type=translation_type,
            is_default=True,
            active=True
        ).first()

        if not template:
            # Fallback to any active template
            template = cls.objects.filter(
                translation_type=translation_type,
                active=True
            ).first()

        return template

    @classmethod
    def create_defaults(cls):
        """Create default prompt templates if they don't exist."""
        # Canonical template (semantic normalization)
        canonical, _ = cls.objects.get_or_create(
            name='Default Canonical',
            defaults={
                'translation_type': 'canonical',
                'system_prompt': (
                    "You are a semantic normalization translator. "
                    "Translate to English (en-US) preserving exact meaning. "
                    "Normalize formatting for text similarity comparison. "
                    "Output only the translated text, no explanations."
                ),
                'developer_prompt': '',
                'user_prompt_template': (
                    "Translate this {original_language} text from {source_platform} ({region}) "
                    "to normalized English:\n\n"
                    "Title: {title}\n"
                    "Description: {description}"
                ),
                'description': 'Default template for canonical translations (semantic normalization for analysis)',
                'active': True,
                'is_default': True,
            }
        )

        # Display template (human-readable)
        display, _ = cls.objects.get_or_create(
            name='Default Display',
            defaults={
                'translation_type': 'display',
                'system_prompt': (
                    "You are a professional translator. "
                    "Translate naturally while preserving tone and cultural nuances. "
                    "Output only the translated text, no explanations."
                ),
                'developer_prompt': '',
                'user_prompt_template': (
                    "Translate this {original_language} content to {target_locale}:\n\n"
                    "Title: {title}\n"
                    "Description: {description}"
                ),
                'description': 'Default template for display translations (human-readable UI)',
                'active': True,
                'is_default': True,
            }
        )

        return canonical, display


class ProviderHealth(models.Model):
    """
    Track health status of translation providers.

    Used for provider failover logic:
    - When a provider encounters billing/quota/auth errors, mark as unavailable
    - When all providers unavailable, system enters STOPPED state
    - Health probes attempt recovery periodically
    - Admin can manually reset providers
    """

    PROVIDER_CHOICES = [
        ('deepl', 'DeepL'),
        ('openai', 'OpenAI'),
    ]

    STATE_CHOICES = [
        ('available', 'Available'),
        ('unavailable_funds', 'Unavailable - Quota/Billing'),
        ('unavailable_auth', 'Unavailable - Authentication'),
        ('unavailable_rate_limit', 'Unavailable - Rate Limited'),
        ('unavailable_transient', 'Unavailable - Transient Error'),
    ]

    provider = models.CharField(
        max_length=20,
        choices=PROVIDER_CHOICES,
        primary_key=True,
        help_text="Translation provider identifier"
    )
    state = models.CharField(
        max_length=30,
        choices=STATE_CHOICES,
        default='available',
        help_text="Current health state of the provider"
    )
    last_error_message = models.TextField(
        null=True,
        blank=True,
        help_text="Last error message encountered"
    )
    last_error_code = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="Last error code (e.g., 'insufficient_quota', '401')"
    )
    last_state_change_at = models.DateTimeField(
        auto_now=True,
        help_text="When the state last changed"
    )
    last_success_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last successful translation timestamp"
    )
    consecutive_failures = models.IntegerField(
        default=0,
        help_text="Number of consecutive failures"
    )

    class Meta:
        verbose_name = "Provider Health"
        verbose_name_plural = "Provider Health Status"

    def __str__(self):
        return f"{self.get_provider_display()} - {self.get_state_display()}"

    @property
    def is_available(self) -> bool:
        """Check if provider is available for use."""
        return self.state == 'available'

    def mark_available(self):
        """Mark provider as available and reset failure counters."""
        self.state = 'available'
        self.last_error_message = None
        self.last_error_code = None
        self.consecutive_failures = 0
        self.last_success_at = timezone.now()
        self.save()

    def mark_unavailable(
        self,
        state: str,
        error_message: str = None,
        error_code: str = None
    ):
        """Mark provider as unavailable with error details."""
        self.state = state
        self.last_error_message = error_message
        self.last_error_code = error_code
        self.consecutive_failures += 1
        self.save()

    def record_success(self):
        """Record a successful translation without changing state."""
        self.last_success_at = timezone.now()
        if self.state == 'available':
            # Reset consecutive failures on success
            self.consecutive_failures = 0
        self.save(update_fields=['last_success_at', 'consecutive_failures'])

    @classmethod
    def get_or_create_provider(cls, provider: str) -> 'ProviderHealth':
        """Get or create a provider health record."""
        obj, _ = cls.objects.get_or_create(provider=provider)
        return obj

    @classmethod
    def get_all_providers(cls) -> dict:
        """Get all provider health records as a dict."""
        providers = {}
        for provider_code, _ in cls.PROVIDER_CHOICES:
            providers[provider_code] = cls.get_or_create_provider(provider_code)
        return providers

    @classmethod
    def reset_all(cls):
        """Reset all providers to available state."""
        for provider_code, _ in cls.PROVIDER_CHOICES:
            health = cls.get_or_create_provider(provider_code)
            health.mark_available()
