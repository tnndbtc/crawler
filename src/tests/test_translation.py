"""
Unit tests for translation module.

Tests:
- TranslationConfig.render_prompts() with various contexts
- Error classification in OpenAI engine
- ProviderHealthManager state transitions
- TranslationManager fallback logic
- API filtering based on health status
"""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Setup Django
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')

import django
django.setup()

from translation.base import (
    TranslationResult,
    TranslationError,
    QuotaExceededError,
    AuthenticationError,
    RateLimitError,
)
from translation.models import ProviderHealth, TranslationConfig, LLMModelConfig


# ============================================================================
# LLMModelConfig.render_prompts() Tests
# ============================================================================

class TestLLMModelConfigRenderPrompts:
    """Tests for LLMModelConfig.render_prompts() method."""

    def test_render_prompts_with_full_context(self):
        """Test rendering prompts with full context."""
        model = LLMModelConfig(
            name='Test Model',
            provider='openai',
            model_id='gpt-4o-mini',
            system_prompt='You are a semantic normalizer for {source_platform}.',
            developer_prompt='Normalize from {original_language} to English.',
            user_prompt='Title: {title}\nDescription: {description}',
        )

        context = {
            'source_platform': 'reddit',
            'region': 'us',
            'original_language': 'ja-JP',
            'title': 'Test Title',
            'description': 'Test Description',
        }

        result = model.render_prompts(context)

        assert result['system'] == 'You are a semantic normalizer for reddit.'
        assert result['developer'] == 'Normalize from ja-JP to English.'
        assert result['user'] == 'Title: Test Title\nDescription: Test Description'

    def test_render_prompts_with_target_locale(self):
        """Test rendering prompts with target locale variable."""
        model = LLMModelConfig(
            name='Display Model',
            provider='openai',
            model_id='gpt-4o-mini',
            system_prompt='You are a translator for {target_locale}.',
            developer_prompt='Translate naturally to {target_locale}.',
            user_prompt='From: {original_language}\nTo: {target_locale}\nText: {title}',
        )

        context = {
            'source_platform': 'youtube',
            'original_language': 'en-US',
            'target_locale': 'zh-Hans',
            'title': 'Hello World',
            'description': '',
        }

        result = model.render_prompts(context)

        assert 'zh-Hans' in result['system']
        assert 'zh-Hans' in result['developer']
        assert 'From: en-US' in result['user']
        assert 'To: zh-Hans' in result['user']

    def test_render_with_empty_developer_prompt(self):
        """Test rendering when developer prompt is empty."""
        model = LLMModelConfig(
            name='Test Model',
            provider='openai',
            model_id='gpt-4o-mini',
            system_prompt='System prompt here.',
            developer_prompt='',  # Empty
            user_prompt='User: {title}',
        )

        context = {'title': 'Test'}

        result = model.render_prompts(context)

        assert result['system'] == 'System prompt here.'
        assert result['developer'] is None  # Should be None when empty
        assert result['user'] == 'User: Test'

    def test_render_with_missing_context_key(self):
        """Test rendering with missing context keys (should leave placeholder)."""
        model = LLMModelConfig(
            name='Test Model',
            provider='openai',
            model_id='gpt-4o-mini',
            system_prompt='Platform: {source_platform}',
            developer_prompt='',
            user_prompt='Title: {title}, Extra: {nonexistent}',
        )

        context = {
            'source_platform': 'twitter',
            'title': 'Test',
            # 'nonexistent' is missing
        }

        result = model.render_prompts(context)

        assert result['system'] == 'Platform: twitter'
        # Missing key should remain as placeholder
        assert '{nonexistent}' in result['user']

    def test_render_with_none_value(self):
        """Test rendering with None value (should convert to empty string)."""
        model = LLMModelConfig(
            name='Test Model',
            provider='openai',
            model_id='gpt-4o-mini',
            system_prompt='Translate text.',
            developer_prompt='',
            user_prompt='Title: {title}\nDesc: {description}',
        )

        context = {
            'title': 'Test Title',
            'description': None,  # None value
        }

        result = model.render_prompts(context)

        assert 'Title: Test Title' in result['user']
        assert 'Desc: ' in result['user']  # None becomes empty string


# ============================================================================
# TranslationConfig.render_prompts() Delegation Tests
# ============================================================================

class TestTranslationConfigRenderPromptsDelegation:
    """Tests for TranslationConfig.render_prompts() delegation to LLMModelConfig."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Clean up LLM models before each test."""
        LLMModelConfig.objects.all().delete()

    def test_render_prompts_delegates_to_canonical_model(self):
        """Test that render_prompts('canonical') delegates to canonical_model."""
        # Create model
        canonical_model = LLMModelConfig.objects.create(
            name='Test Canonical',
            provider='openai',
            model_id='gpt-4o-mini',
            system_prompt='Canonical system for {source_platform}',
            developer_prompt='Canonical dev prompt',
            user_prompt='Canonical user: {title}',
        )

        # Create config with canonical_model set
        config = TranslationConfig(
            canonical_model=canonical_model,
        )

        context = {'source_platform': 'reddit', 'title': 'Test'}
        result = config.render_prompts('canonical', context)

        assert result['system'] == 'Canonical system for reddit'
        assert result['user'] == 'Canonical user: Test'

    def test_render_prompts_delegates_to_display_model(self):
        """Test that render_prompts('display') delegates to display_model."""
        # Create model
        display_model = LLMModelConfig.objects.create(
            name='Test Display',
            provider='openai',
            model_id='gpt-4o-mini',
            system_prompt='Display system for {target_locale}',
            developer_prompt='Display dev prompt',
            user_prompt='Display user: {title}',
        )

        # Create config with display_model set
        config = TranslationConfig(
            display_model=display_model,
        )

        context = {'target_locale': 'zh-Hans', 'title': 'Test'}
        result = config.render_prompts('display', context)

        assert result['system'] == 'Display system for zh-Hans'
        assert result['user'] == 'Display user: Test'

    def test_render_prompts_returns_none_when_no_model(self):
        """Test that render_prompts returns None when no model is set."""
        config = TranslationConfig(
            canonical_model=None,
            display_model=None,
        )

        result = config.render_prompts('canonical', {'title': 'Test'})
        assert result is None

        result = config.render_prompts('display', {'title': 'Test'})
        assert result is None


# ============================================================================
# Error Classification Tests
# ============================================================================

class TestErrorClassification:
    """Tests for error type classification."""

    def test_quota_exceeded_error(self):
        """Test QuotaExceededError is not recoverable."""
        error = QuotaExceededError("Quota exceeded", engine="openai")

        assert error.engine == "openai"
        assert error.recoverable is False
        assert "Quota exceeded" in str(error)

    def test_authentication_error(self):
        """Test AuthenticationError is not recoverable."""
        error = AuthenticationError("Invalid API key", engine="deepl")

        assert error.engine == "deepl"
        assert error.recoverable is False

    def test_rate_limit_error(self):
        """Test RateLimitError is recoverable."""
        error = RateLimitError("Rate limited", engine="openai", retry_after=60)

        assert error.engine == "openai"
        assert error.recoverable is True
        assert error.retry_after == 60

    def test_translation_error_default_recoverable(self):
        """Test base TranslationError defaults to recoverable."""
        error = TranslationError("Generic error", engine="test")

        assert error.engine == "test"
        assert error.recoverable is True


class TestOpenAIErrorCodeClassification:
    """Tests for OpenAI error code classification."""

    def test_quota_error_codes(self):
        """Test that quota-related error codes are recognized."""
        from translation.engines.openai_engine import OPENAI_QUOTA_ERROR_CODES

        quota_codes = {
            'insufficient_quota',
            'billing_hard_limit_reached',
            'billing_not_active',
            'quota_exceeded',
        }

        assert quota_codes.issubset(OPENAI_QUOTA_ERROR_CODES)

    def test_parse_error_code_from_response(self):
        """Test parsing error code from OpenAI error response."""
        # Create mock response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'error': {
                'message': 'You exceeded your current quota',
                'type': 'insufficient_quota',
                'code': 'insufficient_quota'
            }
        }

        # Import and test the engine's parser
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}):
            from translation.engines.openai_engine import OpenAIEngine
            engine = OpenAIEngine(api_key='test-key')

            code = engine._parse_error_code(mock_response)
            assert code == 'insufficient_quota'

    def test_parse_error_code_invalid_response(self):
        """Test parsing error code from malformed response."""
        mock_response = MagicMock()
        mock_response.json.return_value = {'unexpected': 'format'}

        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}):
            from translation.engines.openai_engine import OpenAIEngine
            engine = OpenAIEngine(api_key='test-key')

            code = engine._parse_error_code(mock_response)
            assert code is None


# ============================================================================
# ProviderHealthManager Tests
# ============================================================================

class TestProviderHealthManager:
    """Tests for ProviderHealthManager state transitions."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Reset provider health before each test."""
        # Clear all provider health records
        ProviderHealth.objects.all().delete()

    def test_provider_starts_available(self):
        """Test that new providers start in available state."""
        health = ProviderHealth.get_or_create_provider('openai')

        assert health.state == 'available'
        assert health.is_available is True
        assert health.consecutive_failures == 0

    def test_mark_unavailable_funds(self):
        """Test marking provider unavailable due to quota/billing."""
        health = ProviderHealth.get_or_create_provider('openai')
        health.mark_unavailable(
            state='unavailable_funds',
            error_message='Quota exceeded',
            error_code='insufficient_quota'
        )

        assert health.state == 'unavailable_funds'
        assert health.is_available is False
        assert health.last_error_message == 'Quota exceeded'
        assert health.last_error_code == 'insufficient_quota'
        assert health.consecutive_failures == 1

    def test_mark_unavailable_auth(self):
        """Test marking provider unavailable due to auth failure."""
        health = ProviderHealth.get_or_create_provider('deepl')
        health.mark_unavailable(
            state='unavailable_auth',
            error_message='Invalid API key'
        )

        assert health.state == 'unavailable_auth'
        assert health.is_available is False

    def test_mark_unavailable_rate_limit(self):
        """Test marking provider unavailable due to rate limiting."""
        health = ProviderHealth.get_or_create_provider('openai')
        health.mark_unavailable(
            state='unavailable_rate_limit',
            error_message='Too many requests'
        )

        assert health.state == 'unavailable_rate_limit'
        assert health.is_available is False

    def test_consecutive_failures_increment(self):
        """Test that consecutive failures increment."""
        health = ProviderHealth.get_or_create_provider('openai')

        health.mark_unavailable('unavailable_transient', 'Error 1')
        assert health.consecutive_failures == 1

        health.mark_unavailable('unavailable_transient', 'Error 2')
        assert health.consecutive_failures == 2

        health.mark_unavailable('unavailable_transient', 'Error 3')
        assert health.consecutive_failures == 3

    def test_mark_available_resets_failures(self):
        """Test that marking available resets failure counters."""
        health = ProviderHealth.get_or_create_provider('openai')

        # Simulate failures
        health.mark_unavailable('unavailable_transient', 'Error')
        health.mark_unavailable('unavailable_transient', 'Error')
        assert health.consecutive_failures == 2

        # Recover
        health.mark_available()

        assert health.state == 'available'
        assert health.is_available is True
        assert health.consecutive_failures == 0
        assert health.last_error_message is None
        assert health.last_error_code is None

    def test_record_success_resets_failures(self):
        """Test that recording success resets failures for available provider."""
        health = ProviderHealth.get_or_create_provider('openai')

        # Provider should start available
        health.record_success()

        assert health.consecutive_failures == 0
        assert health.last_success_at is not None

    def test_get_all_providers(self):
        """Test getting all provider health records."""
        providers = ProviderHealth.get_all_providers()

        assert 'openai' in providers
        assert 'deepl' in providers
        assert len(providers) == 2  # Based on PROVIDER_CHOICES

    def test_reset_all_providers(self):
        """Test resetting all providers to available."""
        # Mark providers unavailable
        openai = ProviderHealth.get_or_create_provider('openai')
        openai.mark_unavailable('unavailable_funds', 'Quota error')

        deepl = ProviderHealth.get_or_create_provider('deepl')
        deepl.mark_unavailable('unavailable_auth', 'Auth error')

        # Reset all
        ProviderHealth.reset_all()

        # Verify all are available
        openai.refresh_from_db()
        deepl.refresh_from_db()

        assert openai.is_available is True
        assert deepl.is_available is True


# ============================================================================
# ProviderHealthManager Async Tests
# ============================================================================

@pytest.mark.asyncio
class TestProviderHealthManagerAsync:
    """Async tests for ProviderHealthManager."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Reset provider health before each test."""
        ProviderHealth.objects.all().delete()

    async def test_is_stopped_when_all_unavailable(self):
        """Test system is STOPPED when all providers unavailable."""
        from translation.health import ProviderHealthManager

        # Mark all providers unavailable
        openai = ProviderHealth.get_or_create_provider('openai')
        openai.mark_unavailable('unavailable_funds', 'Quota')

        deepl = ProviderHealth.get_or_create_provider('deepl')
        deepl.mark_unavailable('unavailable_auth', 'Auth')

        # Check stopped state
        is_stopped = await ProviderHealthManager.is_stopped()
        assert is_stopped is True

    async def test_not_stopped_when_one_available(self):
        """Test system is NOT STOPPED when at least one provider available."""
        from translation.health import ProviderHealthManager

        # One unavailable, one available
        openai = ProviderHealth.get_or_create_provider('openai')
        openai.mark_unavailable('unavailable_funds', 'Quota')

        deepl = ProviderHealth.get_or_create_provider('deepl')
        deepl.mark_available()

        # Check stopped state
        is_stopped = await ProviderHealthManager.is_stopped()
        assert is_stopped is False

    async def test_get_available_providers(self):
        """Test getting list of available providers."""
        from translation.health import ProviderHealthManager

        # Setup mixed availability
        openai = ProviderHealth.get_or_create_provider('openai')
        openai.mark_available()

        deepl = ProviderHealth.get_or_create_provider('deepl')
        deepl.mark_unavailable('unavailable_auth', 'Auth')

        available = await ProviderHealthManager.get_available_providers()

        assert 'openai' in available
        assert 'deepl' not in available

    async def test_get_unavailable_providers(self):
        """Test getting list of unavailable providers."""
        from translation.health import ProviderHealthManager

        # Setup mixed availability
        openai = ProviderHealth.get_or_create_provider('openai')
        openai.mark_available()

        deepl = ProviderHealth.get_or_create_provider('deepl')
        deepl.mark_unavailable('unavailable_auth', 'Auth')

        unavailable = await ProviderHealthManager.get_unavailable_providers()

        assert 'deepl' in unavailable
        assert 'openai' not in unavailable

    async def test_get_health_status(self):
        """Test getting full health status."""
        from translation.health import ProviderHealthManager

        # Setup
        openai = ProviderHealth.get_or_create_provider('openai')
        openai.mark_available()

        deepl = ProviderHealth.get_or_create_provider('deepl')
        deepl.mark_unavailable('unavailable_funds', 'Billing error', 'quota_exceeded')

        status = await ProviderHealthManager.get_health_status()

        assert status['status'] == 'degraded'  # One available, one not
        assert status['is_stopped'] is False
        assert 'openai' in status['available_providers']
        assert 'deepl' not in status['available_providers']
        assert status['providers']['deepl']['state'] == 'unavailable_funds'
        assert status['providers']['deepl']['last_error_code'] == 'quota_exceeded'

    async def test_handle_error_marks_unavailable(self):
        """Test that handling error marks provider unavailable."""
        from translation.health import ProviderHealthManager

        # Start with available provider
        openai = ProviderHealth.get_or_create_provider('openai')
        openai.mark_available()

        # Handle quota error
        error = QuotaExceededError("Quota exceeded", engine="openai")
        new_state = await ProviderHealthManager.handle_error(
            'openai', error, error_code='insufficient_quota'
        )

        assert new_state == 'unavailable_funds'

        # Verify in database
        openai.refresh_from_db()
        assert openai.is_available is False

    async def test_mark_success_recovers_provider(self):
        """Test that marking success recovers unavailable provider."""
        from translation.health import ProviderHealthManager

        # Start unavailable
        openai = ProviderHealth.get_or_create_provider('openai')
        openai.mark_unavailable('unavailable_transient', 'Temp error')

        # Mark success
        await ProviderHealthManager.mark_success('openai')

        # Verify recovered
        openai.refresh_from_db()
        assert openai.is_available is True


# ============================================================================
# TranslationResult Tests
# ============================================================================

class TestTranslationResult:
    """Tests for TranslationResult dataclass."""

    def test_success_result(self):
        """Test successful translation result."""
        result = TranslationResult(
            title='Translated Title',
            description='Translated Desc',
            engine='openai',
            source_locale='ja-JP',
            target_locale='en-US',
        )

        assert result.success is True
        assert result.error is None
        assert result.title == 'Translated Title'
        assert result.engine == 'openai'

    def test_error_result(self):
        """Test failed translation result."""
        result = TranslationResult.from_error(
            error='Translation failed',
            engine='deepl',
            source_locale='ko-KR',
            target_locale='en-US'
        )

        assert result.success is False
        assert result.error == 'Translation failed'
        assert result.title == ''
        assert result.engine == 'deepl'

    def test_no_translation_needed(self):
        """Test result when no translation needed."""
        result = TranslationResult.no_translation_needed(
            title='English Title',
            description='English Desc',
            source_locale='en-US',
            target_locale='en-US'
        )

        assert result.success is True
        assert result.engine == 'none'
        assert result.title == 'English Title'
        assert result.metadata.get('reason') == 'same_language'


# ============================================================================
# TranslationConfig Tests
# ============================================================================

class TestTranslationConfig:
    """Tests for TranslationConfig model."""

    def test_singleton_enforcement(self):
        """Test that TranslationConfig enforces singleton pattern."""
        # Get config (creates if needed)
        config1 = TranslationConfig.get_config()
        config2 = TranslationConfig.get_config()

        assert config1.pk == config2.pk == 1

    def test_canonical_locale_locked(self):
        """Test that canonical_locale must be en-US."""
        config = TranslationConfig.get_config()

        with pytest.raises(Exception):  # ValidationError
            config.canonical_locale = 'ja-JP'
            config.save()

    def test_default_fallback_order(self):
        """Test default fallback order is set."""
        config = TranslationConfig.get_config()

        assert len(config.fallback_order) > 0
        assert 'deepl' in config.fallback_order or 'openai' in config.fallback_order

    def test_get_effective_fallback_order_canonical(self):
        """Test effective fallback order for canonical translation."""
        config = TranslationConfig.get_config()
        config.canonical_engine = 'deepl'
        config.fallback_order = ['openai', 'deepl']
        config.save()

        order = config.get_effective_fallback_order('canonical')

        # Primary engine should be first
        assert order[0] == 'deepl'
        # Fallbacks should follow
        assert 'openai' in order

    def test_get_effective_fallback_order_display(self):
        """Test effective fallback order for display translation."""
        config = TranslationConfig.get_config()
        config.display_engine = 'openai'
        config.fallback_order = ['deepl', 'openai']
        config.save()

        order = config.get_effective_fallback_order('display')

        # Primary engine should be first
        assert order[0] == 'openai'


# ============================================================================
# HTTP Status to State Mapping Tests
# ============================================================================

class TestHTTPStatusMapping:
    """Tests for HTTP status code to provider state mapping."""

    def test_status_401_maps_to_auth(self):
        """Test 401 maps to auth unavailable."""
        from translation.health import ProviderHealthManager

        state = ProviderHealthManager.HTTP_STATUS_TO_STATE.get(401)
        assert state == 'unavailable_auth'

    def test_status_403_maps_to_auth(self):
        """Test 403 maps to auth unavailable."""
        from translation.health import ProviderHealthManager

        state = ProviderHealthManager.HTTP_STATUS_TO_STATE.get(403)
        assert state == 'unavailable_auth'

    def test_status_429_maps_to_rate_limit(self):
        """Test 429 maps to rate limit unavailable."""
        from translation.health import ProviderHealthManager

        state = ProviderHealthManager.HTTP_STATUS_TO_STATE.get(429)
        assert state == 'unavailable_rate_limit'

    def test_status_500_maps_to_transient(self):
        """Test 5xx maps to transient unavailable."""
        from translation.health import ProviderHealthManager

        for status in [500, 502, 503, 504]:
            state = ProviderHealthManager.HTTP_STATUS_TO_STATE.get(status)
            assert state == 'unavailable_transient', f"Status {status} should map to transient"


# ============================================================================
# Integration Tests (with mocked external calls)
# ============================================================================

@pytest.mark.asyncio
class TestTranslationManagerFallback:
    """Integration tests for TranslationManager fallback behavior."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Reset state before each test."""
        ProviderHealth.objects.all().delete()

    async def test_returns_system_stopped_when_all_unavailable(self):
        """Test manager returns system_stopped when no providers available."""
        from translation.manager import TranslationManager

        # Mark all providers unavailable
        openai = ProviderHealth.get_or_create_provider('openai')
        openai.mark_unavailable('unavailable_funds', 'Quota')

        deepl = ProviderHealth.get_or_create_provider('deepl')
        deepl.mark_unavailable('unavailable_auth', 'Auth')

        manager = TranslationManager()
        await manager.load_config()

        result = await manager.translate_canonical(
            title='Test',
            description=None,
            source_locale='ja-JP',
        )

        assert result.success is False
        assert result.engine == 'system_stopped'
        assert 'all_providers_unavailable' in result.error

    async def test_skips_english_for_canonical(self):
        """Test manager skips translation for English source."""
        from translation.manager import TranslationManager

        manager = TranslationManager()

        result = await manager.translate_canonical(
            title='English Title',
            description='English Desc',
            source_locale='en-US',
        )

        assert result.success is True
        assert result.engine == 'none'
        assert result.title == 'English Title'

    async def test_skips_same_locale_for_display(self):
        """Test manager skips translation for same source/target locale."""
        from translation.manager import TranslationManager

        manager = TranslationManager()

        result = await manager.translate_display(
            title='Chinese Title',
            description=None,
            source_locale='zh-Hans',
            target_locale='zh-Hans',
        )

        assert result.success is True
        assert result.engine == 'none'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
