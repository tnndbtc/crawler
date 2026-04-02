"""
Django models for translation engine configuration.

Models:
- TranslationConfig: Global translation settings (singleton) with embedded prompts
- LLMModelConfig: LLM model configurations (legacy, kept for backward compatibility)
- ProviderHealth: Track health status of translation providers
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

# ---------------------------------------------------------------------------
# Default prompt templates (used as field defaults in TranslationConfig).
#
# Available variables:
#   Single-text prompts : {source_locale}, {target_locale}, {platform}, {text}
#   Batch prompts       : {source_locale}, {target_locale}, {platform}, {title}, {description}
# ---------------------------------------------------------------------------

CANONICAL_PROMPT_DEFAULT = (
    "You are a professional translator producing machine-readable canonical text.\n"
    "Translate the following text from {source_locale} to normalized English (en-US).\n"
    "Source platform: {platform}\n"
    "Produce a stable, literal, machine-consistent English meaning.\n"
    "Return ONLY the translated text, no explanations.\n\n"
    "{text}"
)

DISPLAY_PROMPT_DEFAULT = (
    "You are a professional news translator with expertise in {platform} content.\n"
    "Translate the following text from {source_locale} to {target_locale}.\n"
    "Translate naturally while preserving tone and cultural nuances.\n"
    "Return ONLY the translated text, no explanations.\n\n"
    "{text}"
)

CANONICAL_BATCH_PROMPT_DEFAULT = (
    "You are a professional translator producing machine-readable canonical text.\n"
    "Translate the following from {source_locale} to normalized English (en-US).\n"
    "Source platform: {platform}\n\n"
    "Instructions:\n"
    "- Title: Translate literally and concisely, preserving the key meaning.\n"
    "- Description: Translate the full meaning accurately and consistently.\n\n"
    "Return JSON only, no markdown fences, no explanations:\n"
    '{"title": "translated title here", "description": "translated description here"}\n\n'
    "Title: {title}\n"
    "Description: {description}"
)

DISPLAY_BATCH_PROMPT_DEFAULT = (
    "You are a professional news translator with expertise in {platform} content.\n"
    "Translate the following from {source_locale} to {target_locale}.\n\n"
    "Instructions:\n"
    "- Title: Keep it punchy and engaging, matching the original tone.\n"
    "- Description: Translate naturally, preserving cultural nuances and context.\n\n"
    "Return JSON only, no markdown fences, no explanations:\n"
    '{"title": "translated title here", "description": "translated description here"}\n\n'
    "Title: {title}\n"
    "Description: {description}"
)


class TranslationConfig(models.Model):
    """
    Global translation configuration (singleton).

    Replaces the old TranslationSettings model with enhanced features:
    - Separate canonical and display engine selection
    - Configurable fallback order
    - Production mode flag
    """

    ENGINE_CHOICES = [
        ('claude', 'Claude CLI'),
    ]

    # Engine selection
    canonical_engine = models.CharField(
        max_length=20,
        choices=ENGINE_CHOICES,
        default='claude',
        help_text="Engine for canonical (en-US) translations used in ranking/analysis"
    )
    display_engine = models.CharField(
        max_length=20,
        choices=ENGINE_CHOICES,
        default='claude',
        help_text="Engine for display translations (human-readable UI)"
    )
    fallback_order = models.JSONField(
        default=list,
        blank=True,
        help_text="Ordered list of fallback engines: ['claude']"
    )

    # LLM Model selection (kept for backward compatibility)
    canonical_model = models.ForeignKey(
        'LLMModelConfig',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='canonical_configs',
        help_text="LLM model config (not used by Claude CLI engine)"
    )
    display_model = models.ForeignKey(
        'LLMModelConfig',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='display_configs',
        help_text="LLM model config (not used by Claude CLI engine)"
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

    # Prompt templates (editable in Django admin)
    canonical_prompt = models.TextField(
        default=CANONICAL_PROMPT_DEFAULT,
        help_text=(
            "Prompt for canonical (en-US) single-text translation. "
            "Variables: {source_locale}, {target_locale}, {platform}, {text}"
        )
    )
    display_prompt = models.TextField(
        default=DISPLAY_PROMPT_DEFAULT,
        help_text=(
            "Prompt for display single-text translation. "
            "Variables: {source_locale}, {target_locale}, {platform}, {text}"
        )
    )
    canonical_batch_prompt = models.TextField(
        default=CANONICAL_BATCH_PROMPT_DEFAULT,
        help_text=(
            "Prompt for canonical (en-US) batch translation (title + description). "
            "Variables: {source_locale}, {target_locale}, {platform}, {title}, {description}"
        )
    )
    display_batch_prompt = models.TextField(
        default=DISPLAY_BATCH_PROMPT_DEFAULT,
        help_text=(
            "Prompt for display batch translation (title + description). "
            "Variables: {source_locale}, {target_locale}, {platform}, {title}, {description}"
        )
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
            self.fallback_order = ['claude']

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
            obj.fallback_order = ['claude']
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

        # Append test-fallback engine if registered (TRANSLATION_TEST_MODE=true)
        import os
        if os.getenv('TRANSLATION_TEST_MODE', 'false').lower() == 'true':
            if 'test-fallback' not in engines:
                engines.append('test-fallback')

        return engines

    def get_llm_model(self, translation_type: str) -> 'LLMModelConfig':
        """
        Get the LLM model config for the given translation type.

        Args:
            translation_type: 'canonical' or 'display'

        Returns:
            LLMModelConfig instance or None
        """
        if translation_type == 'canonical':
            return self.canonical_model
        else:
            return self.display_model

    def render_prompts(self, translation_type: str, context: dict) -> dict:
        """
        Render prompts with context variables for LLM-based translation.

        Delegates to the selected LLMModelConfig for the translation type.

        Args:
            translation_type: 'canonical' or 'display'
            context: Dict with variable values

        Returns:
            Dict with rendered 'system', 'developer', and 'user' prompts,
            or None if no model is configured
        """
        model = self.get_llm_model(translation_type)
        if model:
            return model.render_prompts(context)
        return None


class LLMModelConfig(models.Model):
    """
    LLM model configuration (legacy, kept for backward compatibility).

    Previously used for OpenAI engine prompt management.
    Not used by the Claude CLI engine.
    """

    PROVIDER_CHOICES = [
        ('openai', 'OpenAI'),
        ('anthropic', 'Anthropic'),
    ]

    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Human-readable name (e.g., 'GPT-4o-mini Canonical', 'GPT-4o-mini Display')"
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

    # Model parameters
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

    # Prompts
    system_prompt = models.TextField(
        blank=True,
        default='',
        help_text="System prompt (defines AI behavior)"
    )
    developer_prompt = models.TextField(
        blank=True,
        default='',
        help_text="Developer prompt (additional instructions)"
    )
    user_prompt = models.TextField(
        blank=True,
        default='',
        help_text="User prompt template. Variables: {source_platform}, {original_language}, {target_locale}, {title}, {description}"
    )

    # Status
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

    def render_prompts(self, context: dict) -> dict:
        """
        Render prompts with context variables.

        Args:
            context: Dict with variable values:
                - source_platform: Platform name (reddit, youtube, etc.)
                - original_language: Source locale code
                - target_locale: Target locale code (for display)
                - title: Original title
                - description: Original description

        Returns:
            Dict with rendered 'system', 'developer', and 'user' prompts
        """
        def substitute(template: str, ctx: dict) -> str:
            if not template:
                return ''
            result = template
            for key, value in ctx.items():
                result = result.replace(f'{{{key}}}', str(value) if value else '')
            return result

        return {
            'system': substitute(self.system_prompt, context),
            'developer': substitute(self.developer_prompt, context) if self.developer_prompt else None,
            'user': substitute(self.user_prompt, context),
        }


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
        ('claude', 'Claude CLI'),
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
