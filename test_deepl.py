#!/usr/bin/env python3
"""
Test script to validate DeepL API integration.
This script tests whether the current DeepL implementation works correctly.
"""

import os
import sys

def test_deepl_api():
    """Test DeepL API with the current implementation."""

    # Get API key from environment
    api_key = os.getenv('DEEPL_API_KEY')

    if not api_key:
        print("❌ ERROR: DEEPL_API_KEY environment variable not set")
        return False

    print(f"✅ Found DEEPL_API_KEY: {api_key[:8]}...{api_key[-4:]}")
    print()

    # Try importing deepl
    try:
        import deepl
        print(f"✅ Successfully imported deepl library (version: {deepl.__version__ if hasattr(deepl, '__version__') else 'unknown'})")
    except ImportError as e:
        print(f"❌ Failed to import deepl library: {e}")
        return False

    print()
    print("=" * 60)
    print("Testing DeepL API classes...")
    print("=" * 60)
    print()

    # Test 1: Check what classes are available
    print("Available classes in deepl module:")
    deepl_attrs = [attr for attr in dir(deepl) if not attr.startswith('_')]
    for attr in deepl_attrs:
        obj = getattr(deepl, attr)
        if isinstance(obj, type):  # It's a class
            print(f"  - {attr}")
    print()

    # Test 2: Try the current implementation (deepl.DeepLClient)
    print("Test 1: Using deepl.DeepLClient (current implementation)")
    print("-" * 60)
    try:
        if hasattr(deepl, 'DeepLClient'):
            client = deepl.DeepLClient(api_key)
            print(f"✅ Successfully created DeepLClient instance")

            # Try to translate
            try:
                result = client.translate_text("Hello, world!", target_lang="ES")
                print(f"✅ Translation successful: 'Hello, world!' → '{result.text}'")
                print(f"   Detected source language: {result.detected_source_lang}")
                return True
            except Exception as e:
                print(f"❌ Translation failed: {e}")
                print(f"   Error type: {type(e).__name__}")
                return False
        else:
            print("❌ deepl.DeepLClient class not found")
    except Exception as e:
        print(f"❌ Failed to create DeepLClient: {e}")
        print(f"   Error type: {type(e).__name__}")

    print()

    # Test 3: Try alternative (deepl.Translator)
    print("Test 2: Using deepl.Translator (alternative)")
    print("-" * 60)
    try:
        if hasattr(deepl, 'Translator'):
            translator = deepl.Translator(api_key)
            print(f"✅ Successfully created Translator instance")

            # Try to translate
            try:
                result = translator.translate_text("Hello, world!", target_lang="ES")
                print(f"✅ Translation successful: 'Hello, world!' → '{result.text}'")
                print(f"   Detected source language: {result.detected_source_lang}")
                return True
            except Exception as e:
                print(f"❌ Translation failed: {e}")
                print(f"   Error type: {type(e).__name__}")
                return False
        else:
            print("❌ deepl.Translator class not found")
    except Exception as e:
        print(f"❌ Failed to create Translator: {e}")
        print(f"   Error type: {type(e).__name__}")

    return False


if __name__ == "__main__":
    print("DeepL API Integration Test")
    print("=" * 60)
    print()

    success = test_deepl_api()

    print()
    print("=" * 60)
    if success:
        print("✅ DeepL API test PASSED")
        sys.exit(0)
    else:
        print("❌ DeepL API test FAILED")
        sys.exit(1)
