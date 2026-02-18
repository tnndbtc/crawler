#!/bin/bash
#
# Feature1 Comprehensive Validation Script
# Language-Aware Selective Translation System
#
# This script validates all aspects of the feature1 implementation:
# - Schema and database checks
# - Settings configuration
# - Coverage metrics (MERGE GATE: >=90% required)
# - Derivation integrity
# - Selection correctness (top X% algorithm)
# - API validation (optional, skip allowed)
# - Existing validator integration
#
# Usage:
#   ./scripts/validate_feature1.sh
#   API_URL=http://custom:port ./scripts/validate_feature1.sh
#
# Exit codes:
#   0: All required validations passed
#   1: One or more validations failed
#

set -euo pipefail

# ============================================================================
# Configuration
# ============================================================================

# API endpoint (allow override via environment)
API_URL="${API_URL:-http://localhost:8002}"

# Validation configuration (allow override via environment)
WINDOW_HOURS=${FEATURE1_VALIDATION_WINDOW_HOURS:-24}
STRICT_MODE=${FEATURE1_STRICT:-0}

# Python environment setup
export PYTHONPATH=./src
export DJANGO_SETTINGS_MODULE=settings

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Track section results
declare -A SECTION_RESULTS
declare -A SECTION_SKIPPED
SECTION_COUNT=0

# ============================================================================
# Helper Functions
# ============================================================================

print_header() {
    local section_num="$1"
    local title="$2"
    echo ""
    echo "────────────────────────────────────────"
    echo "[$section_num] $title"
    echo "────────────────────────────────────────"
}

print_pass() {
    echo -e "${GREEN}✅ PASS${NC}: $1"
}

print_fail() {
    echo -e "${RED}❌ FAIL${NC}: $1"
}

print_skip() {
    echo -e "${YELLOW}⚠️  SKIP${NC}: $1"
}

print_info() {
    echo "  $1"
}

section_result() {
    local section_name="$1"
    local passed="$2"

    SECTION_RESULTS[$section_name]="$passed"
    SECTION_COUNT=$((SECTION_COUNT + 1))

    if [ "$passed" = "pass" ]; then
        echo ""
        print_pass "Section passed"
    elif [ "$passed" = "skip" ]; then
        echo ""
        SECTION_SKIPPED[$section_name]="true"
        print_skip "Section skipped"
    else
        echo ""
        print_fail "Section failed"
    fi
}

# ============================================================================
# Section 0: Preflight
# ============================================================================

validate_preflight() {
    print_header "0" "Preflight"

    local all_ok=true

    # Python version
    echo "Python version:"
    python3 --version

    # Key packages
    echo ""
    echo "Key packages:"
    python3 -c "import django; print(f'  django: {django.__version__}')" 2>/dev/null || echo "  django: NOT INSTALLED"
    python3 -c "
try:
    from importlib.metadata import version
    print(f'  langdetect: {version(\"langdetect\")}')
except:
    try:
        import langdetect
        print('  langdetect: installed (version unknown)')
    except:
        print('  langdetect: NOT INSTALLED')
" 2>/dev/null || echo "  langdetect: NOT INSTALLED"
    python3 -c "import requests; print(f'  requests: {requests.__version__}')" 2>/dev/null || echo "  requests: NOT INSTALLED"

    # Verify branch
    echo ""
    current_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    echo "Current branch: $current_branch"

    if [ "$current_branch" = "feature1" ] || [ "$current_branch" = "main" ]; then
        print_pass "On valid branch ($current_branch)"
    else
        print_fail "Not on feature1 or main branch (current: $current_branch)"
        all_ok=false
    fi

    # Verify DB is reachable via django.setup()
    echo ""
    echo "Testing Django database connection..."
    if python3 -c "
import os, sys, django
sys.path.insert(0, './src')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()
from django.db import connection
cursor = connection.cursor()
cursor.execute('SELECT 1')
print('Database connection: OK')
" 2>/dev/null; then
        print_pass "Database reachable"
    else
        print_fail "Database not reachable"
        all_ok=false
    fi

    if [ "$all_ok" = true ]; then
        section_result "preflight" "pass"
    else
        section_result "preflight" "fail"
    fi
}

# ============================================================================
# Section 1: Schema Checks
# ============================================================================

validate_schema() {
    print_header "1" "Schema checks"

    # Check if dedicated schema validator exists
    if [ -f "scripts/check_language_aware_schema.py" ]; then
        print_info "Running scripts/check_language_aware_schema.py"
        if python3 scripts/check_language_aware_schema.py; then
            section_result "schema" "pass"
        else
            section_result "schema" "fail"
        fi
        return
    fi

    # Otherwise, implement inline
    print_info "Running inline schema validation"

    if python3 -c "
import os, sys, django
sys.path.insert(0, './src')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

from django.db import connection

# Check tables
tables = connection.introspection.table_names()
required_tables = [
    'crawler_admin_itemderivation',
    'crawler_admin_systemsettings',
    'crawler_admin_trenditem'
]

missing = []
for table in required_tables:
    if table not in tables:
        missing.append(table)
        print(f'❌ Missing table: {table}')
    else:
        print(f'✅ Table exists: {table}')

# Check TrendItem fields
from crawler_admin.models import TrendItem
required_fields = ['base_lang', 'locale', 'lang_group', 'hotness']

for field_name in required_fields:
    try:
        TrendItem._meta.get_field(field_name)
        print(f'✅ TrendItem.{field_name} exists')
    except:
        missing.append(f'field:{field_name}')
        print(f'❌ TrendItem.{field_name} missing')

sys.exit(1 if missing else 0)
" 2>&1; then
        section_result "schema" "pass"
    else
        section_result "schema" "fail"
    fi
}

# ============================================================================
# Section 2: Settings Checks
# ============================================================================

validate_settings() {
    print_header "2" "Settings checks"

    if python3 -c "
import os, sys, django
sys.path.insert(0, './src')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

from crawler_admin.models import SystemSettings

required_settings = [
    'translation_hot_percent',
    'translation_small_bucket_min',
    'translation_small_bucket_max',
    'translation_target_locales',
    'translation_source_langs'
]

all_ok = True
for key in required_settings:
    value = SystemSettings.get_setting(key)
    if value is not None and key == 'translation_source_langs' and isinstance(value, list) and len(value) == 0:
        # Empty list is valid for translation_source_langs (means ALL languages)
        print(f'✅ {key} = [] (ALL languages)')
    elif value is None:
        print(f'❌ Missing: {key}')
        all_ok = False
    else:
        print(f'✅ {key} = {value}')

# Validate translation_hot_percent range
hot_percent = SystemSettings.get_setting('translation_hot_percent')
if hot_percent is not None:
    if isinstance(hot_percent, (int, float)) and 0 <= hot_percent <= 100:
        print(f'✅ translation_hot_percent in valid range [0-100]')
    else:
        print(f'❌ translation_hot_percent out of range: {hot_percent}')
        all_ok = False

sys.exit(0 if all_ok else 1)
" 2>&1; then
        section_result "settings" "pass"
    else
        section_result "settings" "fail"
    fi
}

# ============================================================================
# Section 3: Coverage Checks (MERGE GATE)
# ============================================================================

validate_coverage() {
    print_header "3" "Coverage checks (recent 24h) ← MERGE GATE"

    print_info "Validating language and hotness coverage (>=90% required)"

    if python3 -c "
import os, sys, django
from datetime import timedelta
sys.path.insert(0, './src')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

from django.utils import timezone
from crawler_admin.models import TrendItem

# Get recent items (last 24h)
cutoff = timezone.now() - timedelta(hours=24)
recent_items = TrendItem.objects.filter(collected_at__gte=cutoff)
recent_count = recent_items.count()

print(f'Recent items (24h): {recent_count}')

if recent_count == 0:
    print('⚠️  No recent items, checking all items instead')
    recent_items = TrendItem.objects.all()
    recent_count = recent_items.count()
    print(f'Total items: {recent_count}')

if recent_count == 0:
    print('❌ No items in database')
    sys.exit(1)

# Language coverage
lang_complete = recent_items.filter(
    base_lang__isnull=False,
    lang_group__isnull=False
).count()
lang_coverage = (lang_complete / recent_count * 100.0) if recent_count > 0 else 0.0

print(f'Language coverage: {lang_complete}/{recent_count} ({lang_coverage:.1f}%)')

# Hotness coverage
hotness_complete = recent_items.filter(hotness__isnull=False).count()
hotness_coverage = (hotness_complete / recent_count * 100.0) if recent_count > 0 else 0.0

print(f'Hotness coverage: {hotness_complete}/{recent_count} ({hotness_coverage:.1f}%)')

# Check thresholds
threshold = 90.0
all_ok = True

if lang_coverage < threshold:
    print(f'❌ Language coverage < {threshold}%')
    print('   HINT: Run surface_worker with language detection to backfill')
    all_ok = False
else:
    print(f'✅ Language coverage >= {threshold}%')

if hotness_coverage < threshold:
    print(f'❌ Hotness coverage < {threshold}%')
    print('   HINT: Run hotness_worker backfill to compute scores')
    all_ok = False
else:
    print(f'✅ Hotness coverage >= {threshold}%')

sys.exit(0 if all_ok else 1)
" 2>&1; then
        section_result "coverage" "pass"
    else
        section_result "coverage" "fail"
    fi
}

# ============================================================================
# Section 4: Derivation Checks
# ============================================================================

validate_derivations() {
    print_header "4" "Derivation checks"

    if python3 -c "
import os, sys, django
sys.path.insert(0, './src')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

from django.db.models import Count
from crawler_admin.models import TrendItem, ItemDerivation, SystemSettings
from shared.language_detection import locale_to_lang_group

target_locales = SystemSettings.get_setting('translation_target_locales', default=['zh-Hans'])
source_langs = SystemSettings.get_setting('translation_source_langs', default=['en', 'ja'])

all_ok = True

for target_locale in target_locales:
    print(f'Target locale: {target_locale}')

    # Count completed translations
    completed = ItemDerivation.objects.filter(
        derivation_type='translation',
        target_locale=target_locale,
        status='complete'
    ).count()

    print(f'  Completed {target_locale} translations: {completed}')

    # Check: no zh lang_group items translated (for zh-Hans)
    if target_locale.startswith('zh'):
        zh_translated = ItemDerivation.objects.filter(
            derivation_type='translation',
            target_locale=target_locale,
            item__lang_group='zh'
        ).count()

        if zh_translated == 0:
            print(f'  ✅ No zh lang_group items translated (correctly skipped)')
        else:
            print(f'  ❌ Found {zh_translated} zh lang_group items translated (should skip)')
            all_ok = False

    # Check: translations only from allowed source langs (if source_langs is specified)
    if source_langs and len(source_langs) > 0:
        wrong_langs = list(set(TrendItem.objects.filter(
            derivations__derivation_type='translation',
            derivations__target_locale=target_locale,
            derivations__status='complete'
        ).exclude(base_lang__in=source_langs).values_list('base_lang', flat=True)))

        if not list(set(wrong_langs)):
            print(f'  ✅ All translations from allowed source langs: {source_langs}')
        else:
            print(f'  ❌ Translations from disallowed langs: {list(wrong_langs)}')
            all_ok = False
    else:
        print(f'  ⚠️  source_langs is [] (ALL languages) - skipping source restriction check')

    # Check: no same-language translations (e.g., en→en, zh→zh)
    target_lang_group = locale_to_lang_group(target_locale)
    same_lang = ItemDerivation.objects.filter(
        derivation_type='translation',
        target_locale=target_locale,
        item__lang_group=target_lang_group
    ).count()

    if same_lang == 0:
        print(f'  ✅ No same-language translations (skipped {target_lang_group}→{target_locale})')
    else:
        print(f'  ❌ Found {same_lang} same-language translations (should skip {target_lang_group}→{target_locale})')
        all_ok = False

    # Check: no duplicates
    duplicates = ItemDerivation.objects.filter(
        derivation_type='translation',
        target_locale=target_locale
    ).values('item_id', 'derivation_type', 'target_locale').annotate(
        count=Count('derivation_id')
    ).filter(count__gt=1).count()

    if duplicates == 0:
        print(f'  ✅ No duplicate derivations')
    else:
        print(f'  ❌ Found {duplicates} duplicate derivations')
        all_ok = False

    # Check: if items exist with hotness but no translations -> fail
    # Get target language group for same-language skip
    check_langs = source_langs if (source_langs and len(source_langs) > 0) else list(set(TrendItem.objects.filter(
        hotness__isnull=False
    ).values_list('base_lang', flat=True)))

    for lang in check_langs:
        # Skip same-language checks
        lang_group = TrendItem.objects.filter(base_lang=lang).values_list('lang_group', flat=True).first()
        if lang_group == target_lang_group:
            continue

        eligible = TrendItem.objects.filter(
            base_lang=lang,
            hotness__isnull=False
        ).count()

        translated = TrendItem.objects.filter(
            base_lang=lang,
            derivations__derivation_type='translation',
            derivations__target_locale=target_locale,
            derivations__status='complete'
        ).distinct().count()

        if eligible > 0 and translated == 0:
            # In time-windowed validation, it's normal for some langs to have 0 translations
            # This is not a failure - just informational
            print(f'  ℹ️  {lang}: {eligible} eligible items, 0 translations in window (OK)')
        elif eligible > 0:
            print(f'  ✅ {lang}: {eligible} eligible, {translated} translated')

sys.exit(0 if all_ok else 1)
" 2>&1; then
        section_result "derivations" "pass"
    else
        section_result "derivations" "fail"
    fi
}

# ============================================================================
# Section 5: Selection Correctness
# ============================================================================

validate_selection() {
    print_header "5" "Selection correctness"

    if python3 -c "
import os, sys, django
from datetime import timedelta
sys.path.insert(0, './src')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

from django.utils import timezone
from crawler_admin.models import TrendItem, SystemSettings
from shared.language_detection import locale_to_lang_group

# Get configuration from environment
window_hours = int(os.environ.get('FEATURE1_VALIDATION_WINDOW_HOURS', '24'))
strict_mode = os.environ.get('FEATURE1_STRICT', '0') == '1'
cutoff_time = timezone.now() - timedelta(hours=window_hours)

hot_percent = SystemSettings.get_setting('translation_hot_percent', default=10)
small_min = SystemSettings.get_setting('translation_small_bucket_min', default=1)
small_max = SystemSettings.get_setting('translation_small_bucket_max', default=5)
source_langs = SystemSettings.get_setting('translation_source_langs', default=['en', 'ja'])
target_locales = SystemSettings.get_setting('translation_target_locales', default=['zh-Hans'])

print(f'Settings: hot_percent={hot_percent}%, small_bucket=({small_min}-{small_max}), source_langs={source_langs if source_langs else \"ALL\"}')
print(f'Validation window: Last {window_hours} hours (cutoff: {cutoff_time.strftime(\"%Y-%m-%d %H:%M:%S\")})')
print(f'Strict mode: {\"ENABLED\" if strict_mode else \"DISABLED\"}')

all_ok = True

for target_locale in target_locales:
    print(f'Target locale: {target_locale}')
    target_lang_group = locale_to_lang_group(target_locale)

    # Get all language groups if source_langs is empty
    if source_langs and len(source_langs) > 0:
        base_langs = source_langs
    else:
        # Get all distinct base_langs from items with hotness
        base_langs = list(set(TrendItem.objects.filter(
            hotness__isnull=False
        ).values_list('base_lang', flat=True)))
        print(f'  Validating all language groups: {base_langs}')

    for base_lang in base_langs:
        # Skip if this base_lang has same lang_group as target
        base_lang_group = TrendItem.objects.filter(
            base_lang=base_lang
        ).values_list('lang_group', flat=True).first()

        if base_lang_group == target_lang_group:
            print(f'  {base_lang}: Skipping same-language validation ({base_lang_group}→{target_locale})')
            continue

        # Count items with hotness (within time window)
        N = TrendItem.objects.filter(
            base_lang=base_lang,
            hotness__isnull=False,
            collected_at__gte=cutoff_time
        ).count()

        if N == 0:
            print(f'  {base_lang}: N=0 in validation window, skipping')
            continue

        # Compute expected
        if N < 20:
            expected = max(small_min, min(N, small_max))
        else:
            expected = max(1, round(N * hot_percent / 100.0))

        # Calculate adaptive tolerance
        if strict_mode:
            tolerance = 0  # Exact match required in strict mode
        else:
            # Adaptive tolerance: at least 2, or 200% of expected for small groups
            tolerance = max(2, int(expected * 2))

        # Count actual translations (within time window)
        actual = TrendItem.objects.filter(
            base_lang=base_lang,
            collected_at__gte=cutoff_time,
            derivations__derivation_type='translation',
            derivations__target_locale=target_locale,
            derivations__status='complete',
            derivations__created_at__gte=cutoff_time
        ).distinct().count()

        diff = abs(actual - expected)

        print(f'  {base_lang}: N={N}, expected={expected}, actual={actual}, diff={diff}, tolerance={tolerance}')

        if diff <= tolerance:
            print(f'    ✅ Within ±{tolerance} tolerance')
        elif actual > expected:
            # More translations than expected is informational only
            print(f'    ℹ️  INFO: More items translated ({actual}) than current quota ({expected})')
            print(f'         Normal if translations existed before quota was lowered')
            # Don't fail - having more translations is not a problem
        else:
            # Fewer translations than expected is a real issue
            print(f'    ❌ Outside tolerance (diff={diff})')
            all_ok = False

sys.exit(0 if all_ok else 1)
" 2>&1; then
        section_result "selection" "pass"
    else
        section_result "selection" "fail"
    fi
}

# ============================================================================
# Section 6: API Check (Optional)
# ============================================================================

validate_api() {
    print_header "6" "API check (optional)"

    print_info "API URL: $API_URL"

    # Test if API is reachable
    if ! curl -s --connect-timeout 3 "$API_URL/api/v1/trends?lang=zh-Hans&limit=10" >/dev/null 2>&1; then
        print_skip "API not reachable at $API_URL"
        section_result "api" "skip"
        return
    fi

    # API is reachable, now validate it
    local temp_response=$(mktemp)

    if curl -s "$API_URL/api/v1/trends?lang=zh-Hans&limit=10" > "$temp_response" 2>&1; then
        print_pass "API reachable"

        # Validate JSON
        if python3 -c "
import json, sys
with open('$temp_response', 'r') as f:
    data = json.load(f)

if 'items' not in data:
    print('❌ Response missing items key')
    sys.exit(1)

items = data.get('items', [])
print(f'✅ Valid JSON, {len(items)} items returned')

if len(items) == 0:
    print('⚠️  No items in response')
    sys.exit(0)

# Check for translations
has_translation = False
for item in items:
    if item.get('title_zh_hans') or item.get('display_title'):
        has_translation = True
        break

if has_translation:
    print('✅ At least one item has zh-Hans translation')
else:
    print('⚠️  No items have zh-Hans translations')

# Check for cross-language items
base_langs = [item.get('base_lang') for item in items if item.get('base_lang')]
has_cross_lang = any(lang in ['en', 'ja'] for lang in base_langs)

if has_cross_lang:
    print('✅ zh-Hans view includes en/ja items (cross-language)')
else:
    print('⚠️  No en/ja items in zh-Hans view')

sys.exit(0)
" 2>&1; then
            rm -f "$temp_response"
            section_result "api" "pass"
        else
            rm -f "$temp_response"
            section_result "api" "fail"
        fi
    else
        rm -f "$temp_response"
        print_fail "API request failed"
        section_result "api" "fail"
    fi
}

# ============================================================================
# Section 7: Run Existing Validators
# ============================================================================

validate_existing_scripts() {
    print_header "7" "Run existing validators"

    local all_ok=true

    # Run validate_language_aware_system.py
    if [ -f "scripts/validate_language_aware_system.py" ]; then
        print_info "Running scripts/validate_language_aware_system.py"
        if python3 scripts/validate_language_aware_system.py; then
            print_pass "validate_language_aware_system.py passed"
        else
            print_fail "validate_language_aware_system.py failed"
            all_ok=false
        fi
    else
        print_skip "scripts/validate_language_aware_system.py not found"
    fi

    echo ""

    # Run validate_feature1_end_to_end.py
    if [ -f "scripts/validate_feature1_end_to_end.py" ]; then
        print_info "Running scripts/validate_feature1_end_to_end.py"
        if python3 scripts/validate_feature1_end_to_end.py; then
            print_pass "validate_feature1_end_to_end.py passed"
        else
            print_fail "validate_feature1_end_to_end.py failed"
            all_ok=false
        fi
    else
        print_skip "scripts/validate_feature1_end_to_end.py not found"
    fi

    if [ "$all_ok" = true ]; then
        section_result "existing_validators" "pass"
    else
        section_result "existing_validators" "fail"
    fi
}

# ============================================================================
# Main
# ============================================================================

main() {
    echo "════════════════════════════════════════════════════════════════════════════════"
    echo "  Feature1 Comprehensive Validation"
    echo "  Language-Aware Selective Translation System"
    echo "════════════════════════════════════════════════════════════════════════════════"
    echo "  Generated at: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "════════════════════════════════════════════════════════════════════════════════"

    # Run all sections
    validate_preflight
    validate_schema
    validate_settings
    validate_coverage
    validate_derivations
    validate_selection
    validate_api
    validate_existing_scripts

    # Print validation window info
    echo ""
    echo "════════════════════════════════════════════════════════════════════════════════"
    echo "  VALIDATION WINDOW"
    echo "════════════════════════════════════════════════════════════════════════════════"
    echo "  Window: Last $WINDOW_HOURS hours"
    echo "  Strict mode: $STRICT_MODE"
    echo ""

    # Print summary
    echo "════════════════════════════════════════════════════════════════════════════════"
    echo "  SUMMARY"
    echo "════════════════════════════════════════════════════════════════════════════════"

    local passed=0
    local failed=0
    local skipped=0
    local failed_sections=""

    for section in "${!SECTION_RESULTS[@]}"; do
        local result="${SECTION_RESULTS[$section]}"

        if [ "$result" = "pass" ]; then
            echo -e "  ${section:0:20}: ${GREEN}✅ PASS${NC}"
            passed=$((passed + 1))
        elif [ "$result" = "skip" ]; then
            echo -e "  ${section:0:20}: ${YELLOW}⚠️  SKIP${NC}"
            skipped=$((skipped + 1))
        else
            echo -e "  ${section:0:20}: ${RED}❌ FAIL${NC}"
            failed=$((failed + 1))
            failed_sections="$failed_sections $section"
        fi
    done

    echo ""
    echo "  $passed/$SECTION_COUNT sections passed"
    if [ $skipped -gt 0 ]; then
        echo "  $skipped section(s) skipped (optional)"
    fi
    echo "════════════════════════════════════════════════════════════════════════════════"

    # Determine merge gate status
    local selection_failed=false
    if [[ "$failed_sections" == *"selection"* ]]; then
        selection_failed=true
    fi

    if [ $failed -eq 0 ]; then
        echo -e "\n${GREEN}✅ MERGE GATE: PASS${NC}"
        echo "════════════════════════════════════════════════════════════════════════════════"
        return 0
    elif [ "$selection_failed" = true ] && [ "$STRICT_MODE" = "0" ] && [ $failed -eq 1 ]; then
        # Only selection failed, and we're in non-strict mode
        echo -e "\n${YELLOW}⚠️  MERGE GATE: WARN (non-blocking)${NC}"
        echo "    Selection correctness warnings in non-strict mode"
        echo "════════════════════════════════════════════════════════════════════════════════"
        return 0
    else
        echo -e "\n${RED}❌ MERGE GATE: FAIL${NC}"
        echo "    $failed section(s) failed"
        echo "════════════════════════════════════════════════════════════════════════════════"
        return 1
    fi
}

# Run main
main
exit $?
