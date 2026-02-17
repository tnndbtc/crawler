#!/usr/bin/env python3
"""
Validation script for language-aware selective translation system.

This script validates that the language-aware ingestion system is working correctly:
1. Language detection coverage
2. Hotness score distribution
3. Translation coverage per lang_group
4. Derivation table integrity
5. Settings configuration

Usage:
    cd /home/tnnd/data/code/crawler
    python scripts/validate_language_aware_system.py
"""

import os
import sys
import django
from datetime import datetime, timedelta

# Setup Django
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

from django.db.models import Count, Avg, Min, Max, Q
from django.utils import timezone
from crawler_admin.models import TrendItem, ItemDerivation, SystemSettings


def print_section(title):
    """Print section header."""
    print(f"\n{'=' * 80}")
    print(f"  {title}")
    print('=' * 80)


def validate_language_detection():
    """Validate language detection coverage."""
    print_section("1. Language Detection Coverage")

    total_items = TrendItem.objects.count()
    items_with_lang = TrendItem.objects.filter(base_lang__isnull=False).count()

    if total_items == 0:
        print("⚠️  No items in database")
        return False

    coverage_pct = (items_with_lang / total_items * 100.0)

    print(f"Total items: {total_items}")
    print(f"Items with base_lang: {items_with_lang}")
    print(f"Coverage: {coverage_pct:.1f}%")

    # Distribution by language group
    lang_distribution = TrendItem.objects.values('lang_group').annotate(
        count=Count('id')
    ).order_by('-count')

    print("\nLanguage group distribution:")
    for item in lang_distribution[:10]:
        lang_group = item['lang_group'] or 'NULL'
        count = item['count']
        pct = (count / total_items * 100.0)
        print(f"  {lang_group:10s}: {count:6d} ({pct:5.1f}%)")

    # Check recent items (last 24h)
    cutoff = timezone.now() - timedelta(hours=24)
    recent_total = TrendItem.objects.filter(collected_at__gte=cutoff).count()
    recent_with_lang = TrendItem.objects.filter(
        collected_at__gte=cutoff,
        base_lang__isnull=False
    ).count()

    if recent_total > 0:
        recent_coverage = (recent_with_lang / recent_total * 100.0)
        print(f"\nRecent items (24h):")
        print(f"  Total: {recent_total}")
        print(f"  With language: {recent_with_lang}")
        print(f"  Coverage: {recent_coverage:.1f}%")

        if recent_coverage >= 90:
            print("  ✅ PASS: Recent coverage >= 90%")
            return True
        else:
            print(f"  ❌ FAIL: Recent coverage < 90% ({recent_coverage:.1f}%)")
            return False
    else:
        if coverage_pct >= 90:
            print("  ✅ PASS: Overall coverage >= 90%")
            return True
        else:
            print(f"  ⚠️  WARN: Overall coverage < 90% ({coverage_pct:.1f}%)")
            return coverage_pct >= 50  # At least 50% to pass


def validate_hotness_scoring():
    """Validate hotness score distribution."""
    print_section("2. Hotness Score Distribution")

    total_items = TrendItem.objects.count()
    items_with_hotness = TrendItem.objects.filter(hotness__isnull=False).count()

    if total_items == 0:
        print("⚠️  No items in database")
        return False

    coverage_pct = (items_with_hotness / total_items * 100.0)

    print(f"Total items: {total_items}")
    print(f"Items with hotness: {items_with_hotness}")
    print(f"Coverage: {coverage_pct:.1f}%")

    if items_with_hotness == 0:
        print("❌ FAIL: No items have hotness scores")
        return False

    # Statistics
    stats = TrendItem.objects.filter(hotness__isnull=False).aggregate(
        avg=Avg('hotness'),
        min=Min('hotness'),
        max=Max('hotness')
    )

    print(f"\nHotness statistics:")
    print(f"  Average: {stats['avg']:.2f}")
    print(f"  Min: {stats['min']:.2f}")
    print(f"  Max: {stats['max']:.2f}")

    # Check if scores are in expected range
    if stats['max'] > 1000000:
        print("  ⚠️  WARN: Maximum hotness unusually high (>1M)")

    # Distribution by range
    ranges = [
        (500, float('inf'), 'Top 1% (>500)'),
        (300, 500, 'Top 5% (300-500)'),
        (200, 300, 'Top 10% (200-300)'),
        (100, 200, 'Top 25% (100-200)'),
        (50, 100, 'Top 50% (50-100)'),
        (0, 50, 'Bottom 50% (<50)'),
    ]

    print("\nHotness distribution:")
    for min_val, max_val, label in ranges:
        if max_val == float('inf'):
            count = TrendItem.objects.filter(hotness__gte=min_val).count()
        else:
            count = TrendItem.objects.filter(
                hotness__gte=min_val,
                hotness__lt=max_val
            ).count()

        if items_with_hotness > 0:
            pct = (count / items_with_hotness * 100.0)
            print(f"  {label:20s}: {count:6d} ({pct:5.1f}%)")

    # Check recent items
    cutoff = timezone.now() - timedelta(hours=24)
    recent_total = TrendItem.objects.filter(collected_at__gte=cutoff).count()
    recent_with_hotness = TrendItem.objects.filter(
        collected_at__gte=cutoff,
        hotness__isnull=False
    ).count()

    if recent_total > 0:
        recent_coverage = (recent_with_hotness / recent_total * 100.0)
        print(f"\nRecent items (24h):")
        print(f"  Total: {recent_total}")
        print(f"  With hotness: {recent_with_hotness}")
        print(f"  Coverage: {recent_coverage:.1f}%")

        if recent_coverage >= 90:
            print("  ✅ PASS: Recent coverage >= 90%")
            return True
        else:
            print(f"  ⚠️  WARN: Recent coverage < 90% ({recent_coverage:.1f}%)")
            return recent_coverage >= 50  # At least 50% to pass
    else:
        if coverage_pct >= 90:
            print("  ✅ PASS: Overall coverage >= 90%")
            return True
        else:
            print(f"  ⚠️  WARN: Overall coverage < 90% ({coverage_pct:.1f}%)")
            return coverage_pct >= 50


def validate_translation_coverage():
    """Validate translation coverage per lang_group."""
    print_section("3. Translation Coverage by Language Group")

    # Get settings
    hot_percent = SystemSettings.get_setting('translation_hot_percent', default=10)
    source_langs = SystemSettings.get_setting('translation_source_langs', default=['en', 'ja'])
    target_locales = SystemSettings.get_setting('translation_target_locales', default=['zh-Hans'])

    print(f"Settings:")
    print(f"  translation_hot_percent: {hot_percent}%")
    print(f"  translation_source_langs: {source_langs if source_langs else 'ALL'}")
    print(f"  translation_target_locales: {target_locales}")

    all_passed = True

    for target_locale in target_locales:
        print(f"\nTarget locale: {target_locale}")

        # Per-source translation breakdown
        derivation_count = ItemDerivation.objects.filter(
            derivation_type='translation',
            target_locale=target_locale,
            status='complete'
        ).count()

        # Try to count inline translations (may not exist in all versions)
        inline_field = f"display_title_{target_locale.lower().replace('-', '_')}"
        try:
            inline_count = TrendItem.objects.exclude(**{inline_field: ''}).exclude(**{inline_field: None}).count()
        except Exception:
            inline_count = 0

        print(f"  Translation sources:")
        print(f"    Source A (ItemDerivation): {derivation_count}")
        if inline_count > 0:
            print(f"    Source B (inline display_*): {inline_count}")

        # If source_langs is empty, validate all language groups
        if source_langs and len(source_langs) > 0:
            base_langs = source_langs
        else:
            # Get all distinct base_langs from items with hotness
            # Use set() to force evaluation and get truly distinct values
            base_langs = list(set(TrendItem.objects.filter(
                hotness__isnull=False
            ).values_list('base_lang', flat=True)))
            print(f"  Validating all language groups: {base_langs}")

        for base_lang in base_langs:
            # Count items
            total = TrendItem.objects.filter(base_lang=base_lang).count()
            with_hotness = TrendItem.objects.filter(
                base_lang=base_lang,
                hotness__isnull=False
            ).count()

            # Count translated items (Source A: ItemDerivation only)
            translated = TrendItem.objects.filter(
                base_lang=base_lang,
                derivations__derivation_type='translation',
                derivations__target_locale=target_locale,
                derivations__status='complete'
            ).distinct().count()

            if with_hotness == 0:
                coverage_pct = 0.0
            else:
                coverage_pct = (translated / with_hotness * 100.0)

            print(f"  {base_lang}:")
            print(f"    Total items: {total}")
            print(f"    Items with hotness: {with_hotness}")
            print(f"    Translated (ItemDerivation): {translated}")
            print(f"    Coverage: {coverage_pct:.1f}%")

            # Check if coverage is reasonable
            # Should be close to hot_percent, but may vary
            if with_hotness > 0:
                expected_range_low = hot_percent * 0.5  # 50% of expected
                expected_range_high = hot_percent * 2.0  # 200% of expected

                if coverage_pct < expected_range_low:
                    print(f"    ⚠️  WARN: Coverage below expected range ({hot_percent}%)")
                    all_passed = False
                elif coverage_pct > expected_range_high:
                    print(f"    ⚠️  WARN: Coverage above expected range ({hot_percent}%)")
                else:
                    print(f"    ✅ PASS: Coverage within expected range")

    return all_passed


def validate_derivation_integrity():
    """Validate ItemDerivation table integrity."""
    print_section("4. ItemDerivation Table Integrity")

    total_derivations = ItemDerivation.objects.count()
    print(f"Total derivations: {total_derivations}")

    if total_derivations == 0:
        print("⚠️  No derivations in database (may be expected if translation hasn't run)")
        return True  # Not a failure if system is new

    # Check for duplicates
    duplicate_check = ItemDerivation.objects.values(
        'item_id', 'derivation_type', 'target_locale'
    ).annotate(count=Count('derivation_id')).filter(count__gt=1)

    duplicate_count = duplicate_check.count()
    print(f"\nDuplicate check:")
    print(f"  Duplicate derivations: {duplicate_count}")

    if duplicate_count > 0:
        print("  ❌ FAIL: Found duplicate derivations (violates unique constraint)")
        for dup in duplicate_check[:5]:
            print(f"    item_id={dup['item_id']}, type={dup['derivation_type']}, "
                  f"locale={dup['target_locale']}, count={dup['count']}")
        return False
    else:
        print("  ✅ PASS: No duplicate derivations")

    # Status distribution
    status_distribution = ItemDerivation.objects.values('status').annotate(
        count=Count('derivation_id')
    ).order_by('-count')

    print(f"\nStatus distribution:")
    for item in status_distribution:
        status = item['status']
        count = item['count']
        pct = (count / total_derivations * 100.0)
        print(f"  {status:10s}: {count:6d} ({pct:5.1f}%)")

    # Check failed rate
    failed_count = ItemDerivation.objects.filter(status='failed').count()
    failed_pct = (failed_count / total_derivations * 100.0) if total_derivations > 0 else 0

    if failed_pct > 20:
        print(f"  ⚠️  WARN: High failure rate ({failed_pct:.1f}%)")
        return False
    elif failed_pct > 5:
        print(f"  ⚠️  WARN: Moderate failure rate ({failed_pct:.1f}%)")
        return True
    else:
        print(f"  ✅ PASS: Low failure rate ({failed_pct:.1f}%)")
        return True


def validate_settings_configuration():
    """Validate SystemSettings configuration."""
    print_section("5. SystemSettings Configuration")

    required_settings = [
        'translation_hot_percent',
        'translation_small_bucket_min',
        'translation_small_bucket_max',
        'translation_target_locales',
        'translation_source_langs',
    ]

    all_present = True

    for setting_key in required_settings:
        value = SystemSettings.get_setting(setting_key)

        # Special case: translation_source_langs can be None or empty list (meaning ALL languages)
        if value is None and setting_key != 'translation_source_langs':
            print(f"  ❌ {setting_key}: NOT SET")
            all_present = False
        elif setting_key == 'translation_source_langs' and (value is None or value == []):
            display_value = "[] (ALL languages)" if value == [] else "null (ALL languages)"
            print(f"  ✅ {setting_key}: {display_value}")
        else:
            print(f"  ✅ {setting_key}: {value}")

    if all_present:
        print("\n✅ PASS: All required settings present")
        return True
    else:
        print("\n❌ FAIL: Missing required settings")
        return False


def main():
    """Run all validations."""
    print("=" * 80)
    print("  Language-Aware Selective Translation System - Validation Report")
    print("=" * 80)
    print(f"  Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    results = {}

    # Run all validations
    results['language_detection'] = validate_language_detection()
    results['hotness_scoring'] = validate_hotness_scoring()
    results['translation_coverage'] = validate_translation_coverage()
    results['derivation_integrity'] = validate_derivation_integrity()
    results['settings_configuration'] = validate_settings_configuration()

    # Summary
    print_section("Summary")

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {test_name:25s}: {status}")

    print(f"\nOverall: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All validations passed!")
        return 0
    else:
        print(f"\n⚠️  {total - passed} validation(s) failed")
        return 1


if __name__ == '__main__':
    sys.exit(main())
