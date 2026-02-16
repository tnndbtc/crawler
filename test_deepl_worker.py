#!/usr/bin/env python3
"""
Test script to debug why DeepL is never used as provider in translation worker.
This mimics the actual worker code path to identify the issue.
"""

import os
import sys
import asyncio

# Setup Django (same as translation_worker.py)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')

import django
django.setup()

from crawler_api.translation.providers import get_provider, TranslationError, DeepLQuotaExceededError


async def test_deepl_provider():
    """Test DeepL provider with actual worker code path."""

    print("=" * 70)
    print("Testing DeepL Provider (mimicking translation_worker.py)")
    print("=" * 70)
    print()

    # Test cases: common locale pairs
    test_cases = [
        ("Hello, world!", "en-US", "ja-JP"),
        ("こんにちは世界", "ja-JP", "en-US"),
        ("你好世界", "zh-Hans", "en-US"),
        ("Hello, world!", "en-US", "zh-Hans"),
        ("Bonjour le monde", "fr-FR", "en-US"),
    ]

    # Get provider (same way as worker does)
    try:
        print("Getting DeepL provider...")
        provider = get_provider("deepl")
        print(f"✅ Successfully created provider: {provider.__class__.__name__}")
        print()
    except Exception as e:
        print(f"❌ Failed to create provider: {e}")
        print(f"   Error type: {type(e).__name__}")
        return False

    success_count = 0
    fail_count = 0

    for text, source_locale, target_locale in test_cases:
        print(f"Test: '{text[:30]}...' | {source_locale} → {target_locale}")
        print("-" * 70)

        try:
            # This is exactly how translation_worker.py calls it (lines 229-233)
            result = await provider.translate(
                text=text,
                source_locale=source_locale,
                target_locale=target_locale
            )
            print(f"✅ SUCCESS: '{result[:50]}...'")
            success_count += 1
        except DeepLQuotaExceededError as e:
            print(f"⚠️  QUOTA EXCEEDED: {e}")
            print(f"   This would trigger fallback to argostranslate")
            fail_count += 1
        except TranslationError as e:
            print(f"❌ TRANSLATION ERROR: {e}")
            print(f"   Error type: {type(e).__name__}")
            print(f"   This would trigger fallback to argostranslate")
            fail_count += 1
        except Exception as e:
            print(f"❌ UNEXPECTED ERROR: {e}")
            print(f"   Error type: {type(e).__name__}")
            print(f"   This would trigger fallback to argostranslate")
            import traceback
            traceback.print_exc()
            fail_count += 1

        print()

    print("=" * 70)
    print(f"Results: {success_count} succeeded, {fail_count} failed")
    print("=" * 70)

    if fail_count > 0:
        print()
        print("⚠️  DeepL is failing, which explains why argostranslate is always used!")
        print("   The worker code catches these errors and falls back to argostranslate.")
        return False
    else:
        print()
        print("✅ All DeepL translations succeeded!")
        print("   The issue must be elsewhere in the worker logic.")
        return True


async def test_locale_mapping():
    """Test DeepL locale mapping specifically."""
    print()
    print("=" * 70)
    print("Testing DeepL Locale Mapping")
    print("=" * 70)
    print()

    from crawler_api.translation.providers import DeepLTranslationProvider

    provider = DeepLTranslationProvider()

    # Test locales that might be in the database
    test_locales = [
        "en-US", "en-GB", "ja-JP", "zh-Hans", "zh-Hant",
        "ko-KR", "es-ES", "fr-FR", "de-DE", "ru-RU",
        "ar-SA", "pt-PT", "pt-BR", "it-IT", "nl-NL", "pl-PL"
    ]

    print("Locale mapping (source vs target):")
    for locale in test_locales:
        source_mapped = provider._map_locale_source(locale)
        target_mapped = provider._map_locale_target(locale)
        in_map = locale in provider.LOCALE_MAP
        print(f"  {locale:12} → source: {source_mapped:8} target: {target_mapped:8} {'✅' if in_map else '⚠️ '}")

    print()


if __name__ == "__main__":
    print("DeepL Translation Worker Debug Test")
    print()

    try:
        # Test locale mapping first
        asyncio.run(test_locale_mapping())

        # Test actual translations
        success = asyncio.run(test_deepl_provider())

        print()
        if success:
            print("✅ DeepL is working correctly. Check worker logs for other issues.")
            sys.exit(0)
        else:
            print("❌ DeepL is failing. This explains the argostranslate fallback.")
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
