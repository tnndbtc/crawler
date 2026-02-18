#!/usr/bin/env python3
"""
Verification script for translation selection quota fix.

This script verifies that the translation selection logic:
1. Calculates percentages from TOTAL items (including already-translated)
2. Stops selecting when quota is filled
3. Correctly handles edge cases (manual translations, small buckets)

Usage:
    python verify_translation_selection_fix.py
"""

import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

from crawler_admin.models import TrendItem, ItemDerivation, SystemSettings
from shared.translation_selection import select_items_for_translation


def print_section(title):
    """Print a section header."""
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}")


def get_stats(target_locale='zh-Hans'):
    """Get current translation statistics."""
    settings_hot_percent = SystemSettings.get_setting('translation_hot_percent', default=10)

    stats = {}

    # Get lang groups from items with hotness
    lang_groups = TrendItem.objects.filter(
        hotness__isnull=False
    ).values_list('lang_group', flat=True).distinct()

    for lang_group in lang_groups:
        if not lang_group:
            continue

        # Total items with hotness
        total = TrendItem.objects.filter(
            lang_group=lang_group,
            hotness__isnull=False
        ).count()

        # Already translated
        translated = ItemDerivation.objects.filter(
            item__lang_group=lang_group,
            item__hotness__isnull=False,
            derivation_type='translation',
            target_locale=target_locale,
            status='complete'
        ).count()

        if total > 0:
            actual_pct = (translated / total) * 100

            # Calculate target based on small bucket logic
            if total < 20:
                small_min = SystemSettings.get_setting('translation_small_bucket_min', default=1)
                small_max = SystemSettings.get_setting('translation_small_bucket_max', default=5)
                target = max(small_min, min(total, small_max))
            else:
                target = max(1, int(total * settings_hot_percent / 100.0))

            stats[lang_group] = {
                'total': total,
                'translated': translated,
                'target': target,
                'needed': max(0, target - translated),
                'actual_pct': actual_pct,
            }

    return stats


def verify_selection_logic():
    """Verify the selection logic works correctly."""
    print_section("Translation Selection Logic Verification")

    target_locale = 'zh-Hans'

    # Get current settings
    hot_percent = SystemSettings.get_setting('translation_hot_percent', default=10)
    small_min = SystemSettings.get_setting('translation_small_bucket_min', default=1)
    small_max = SystemSettings.get_setting('translation_small_bucket_max', default=5)

    print(f"\nSettings:")
    print(f"  - translation_hot_percent: {hot_percent}%")
    print(f"  - translation_small_bucket_min: {small_min}")
    print(f"  - translation_small_bucket_max: {small_max}")
    print(f"  - target_locale: {target_locale}")

    # Get BEFORE stats
    print_section("BEFORE Selection")
    before_stats = get_stats(target_locale)

    if not before_stats:
        print("\n⚠️  No items found with hotness scores!")
        print("   Please ensure you have TrendItems with hotness scores in the database.")
        return False

    print(f"\n{'Lang Group':<12} {'Total':<8} {'Translated':<12} {'Target':<8} {'Needed':<8} {'Actual %':<10}")
    print(f"{'-'*70}")

    for lang_group, data in sorted(before_stats.items()):
        print(f"{lang_group:<12} {data['total']:<8} {data['translated']:<12} "
              f"{data['target']:<8} {data['needed']:<8} {data['actual_pct']:<10.3f}%")

    # Run selection
    print_section("Running Selection")
    selected_items = select_items_for_translation(target_locale, limit=1000)

    print(f"\nSelected {len(selected_items)} items for translation")

    if selected_items:
        by_lang = {}
        for item in selected_items:
            lang = item.lang_group or 'unknown'
            by_lang[lang] = by_lang.get(lang, 0) + 1

        print(f"\nBreakdown by language group:")
        for lang, count in sorted(by_lang.items()):
            print(f"  - {lang}: {count} items")

    # Verify expectations
    print_section("Verification Results")

    all_tests_passed = True

    for lang_group, before in sorted(before_stats.items()):
        selected_count = sum(1 for item in selected_items if item.lang_group == lang_group)
        expected_count = before['needed']

        if selected_count == expected_count:
            status = "✅ PASS"
        else:
            status = "❌ FAIL"
            all_tests_passed = False

        print(f"\n{status} {lang_group}:")
        print(f"  Total items:        {before['total']}")
        print(f"  Already translated: {before['translated']}")
        print(f"  Target count:       {before['target']}")
        print(f"  Expected selection: {expected_count}")
        print(f"  Actual selection:   {selected_count}")

        if selected_count != expected_count:
            print(f"  ⚠️  MISMATCH: Expected {expected_count}, got {selected_count}")

    # Check if any lang_group has quota already filled
    print_section("Quota Status")

    quota_filled = []
    quota_partial = []
    quota_empty = []

    for lang_group, before in sorted(before_stats.items()):
        if before['needed'] == 0:
            quota_filled.append(lang_group)
        elif before['translated'] > 0:
            quota_partial.append(lang_group)
        else:
            quota_empty.append(lang_group)

    if quota_filled:
        print(f"\n✅ Quota FILLED (should select 0 items):")
        for lang in quota_filled:
            print(f"  - {lang}: {before_stats[lang]['translated']}/{before_stats[lang]['target']} translated")

    if quota_partial:
        print(f"\n⚠️  Quota PARTIAL (should select remaining items):")
        for lang in quota_partial:
            stats = before_stats[lang]
            print(f"  - {lang}: {stats['translated']}/{stats['target']} translated, need {stats['needed']} more")

    if quota_empty:
        print(f"\n📋 Quota EMPTY (should select target count):")
        for lang in quota_empty:
            print(f"  - {lang}: 0/{before_stats[lang]['target']} translated, need {before_stats[lang]['target']}")

    # Final summary
    print_section("Summary")

    if all_tests_passed:
        print("\n✅ ALL TESTS PASSED!")
        print("\nThe selection logic is working correctly:")
        print("  ✓ Calculates percentages from TOTAL items (including already-translated)")
        print("  ✓ Stops selecting when quota is filled (needed = 0)")
        print("  ✓ Selects only the needed count to fill quota")
    else:
        print("\n❌ SOME TESTS FAILED!")
        print("\nPlease review the mismatches above.")

    return all_tests_passed


def main():
    """Main entry point."""
    try:
        success = verify_selection_logic()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
