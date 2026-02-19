#!/bin/bash
#
# Good Citizen Feature Validation Script
#
# Tests all Good Citizen features:
# - Rate limiting
# - Circuit breaker
# - HTTP caching
# - Jitter and backoff
# - Domain policies
# - Observability (domain_report)
#

set -e  # Exit on error

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Track results
TESTS_PASSED=0
TESTS_FAILED=0
TESTS_TOTAL=0

# Helper functions
print_header() {
    echo ""
    echo -e "${BLUE}${BOLD}======================================================================${NC}"
    echo -e "${BLUE}${BOLD}  $1${NC}"
    echo -e "${BLUE}${BOLD}======================================================================${NC}"
    echo ""
}

print_step() {
    TESTS_TOTAL=$((TESTS_TOTAL + 1))
    echo -e "${BOLD}[$TESTS_TOTAL] $1${NC}"
}

print_success() {
    TESTS_PASSED=$((TESTS_PASSED + 1))
    echo -e "    ${GREEN}✓${NC} $1"
}

print_error() {
    TESTS_FAILED=$((TESTS_FAILED + 1))
    echo -e "    ${RED}✗${NC} $1"
}

print_warning() {
    echo -e "    ${YELLOW}⚠${NC} $1"
}

print_summary() {
    echo ""
    echo -e "${BLUE}${BOLD}======================================================================${NC}"
    echo -e "${BOLD}Test Summary${NC}"
    echo -e "${BLUE}${BOLD}======================================================================${NC}"
    echo -e "Total tests:  ${TESTS_TOTAL}"
    echo -e "Passed:       ${GREEN}${TESTS_PASSED}${NC}"
    echo -e "Failed:       ${RED}${TESTS_FAILED}${NC}"
    echo ""

    if [ $TESTS_FAILED -eq 0 ]; then
        echo -e "${GREEN}${BOLD}✅ ALL TESTS PASSED${NC}"
        echo -e "${GREEN}Good Citizen features are working correctly!${NC}"
        echo ""
        return 0
    else
        echo -e "${RED}${BOLD}❌ SOME TESTS FAILED${NC}"
        echo -e "${RED}Please review the errors above${NC}"
        echo ""
        return 1
    fi
}

# Change to project root
cd "$(dirname "$0")/.."

print_header "Good Citizen Feature Validation"

# Test 1: Database migrations
print_step "Database Migrations"
if python manage.py showmigrations crawler_admin | grep -q "0011_good_citizen_infrastructure"; then
    if python manage.py showmigrations crawler_admin | grep -q "\[X\] 0011_good_citizen_infrastructure"; then
        print_success "Migration 0011_good_citizen_infrastructure is applied"
    else
        print_warning "Migration exists but not applied, applying now..."
        python manage.py migrate crawler_admin 0011_good_citizen_infrastructure --no-input > /dev/null 2>&1
        if [ $? -eq 0 ]; then
            print_success "Migration applied successfully"
        else
            print_error "Failed to apply migration"
        fi
    fi
else
    print_error "Migration 0011_good_citizen_infrastructure not found"
fi

# Test 2: Database models
print_step "Database Models"
if python manage.py shell -c "from crawler_admin.models import DomainPolicy, URLCache, DomainMetrics; print('OK')" 2>/dev/null | grep -q "OK"; then
    print_success "Models (DomainPolicy, URLCache, DomainMetrics) loaded successfully"
else
    print_error "Failed to load Good Citizen models"
fi

# Test 3: Feature flags
print_step "Feature Flags"
FEATURE_FLAGS=$(python manage.py shell -c "
from crawler_admin.models import SystemSettings
flags = ['enable_rate_limiting', 'enable_circuit_breaker', 'enable_http_caching']
for flag in flags:
    try:
        value = SystemSettings.get_setting(flag, default=False)
        print(f'{flag}={value}')
    except:
        print(f'{flag}=ERROR')
" 2>/dev/null)

if echo "$FEATURE_FLAGS" | grep -q "enable_rate_limiting="; then
    print_success "Feature flags accessible via SystemSettings"
    echo "$FEATURE_FLAGS" | sed 's/^/      /'
else
    print_error "Failed to read feature flags"
fi

# Test 4: Shared utilities
print_step "Shared Utilities"
cd src
if python -c "from shared.jitter import jitter, exponential_backoff; from shared.url_utils import normalize_url, compute_url_hash; print('OK')" 2>/dev/null | grep -q "OK"; then
    print_success "Utility modules (jitter, url_utils) loaded successfully"
else
    print_error "Failed to load utility modules"
fi
cd ..

# Test 5: RateLimitedClient
print_step "RateLimitedClient"
cd src
if python -c "from shared.http_client import RateLimitedClient, CircuitBreakerOpen, RateLimitExceeded; print('OK')" 2>/dev/null | grep -q "OK"; then
    print_success "RateLimitedClient loaded successfully"
else
    print_error "Failed to load RateLimitedClient"
fi
cd ..

# Test 6: RateLimitedClient without Django
print_step "RateLimitedClient (Django-free mode)"
DJANGO_FREE_TEST=$(python -c "
import sys
import os

# Don't configure Django
os.environ.pop('DJANGO_SETTINGS_MODULE', None)

sys.path.insert(0, 'src')
try:
    from shared.http_client import RateLimitedClient
    print('OK')
except Exception as e:
    print(f'ERROR: {e}')
" 2>&1)

if echo "$DJANGO_FREE_TEST" | grep -q "OK"; then
    print_success "RateLimitedClient works without Django configuration"
else
    print_error "RateLimitedClient requires Django: $DJANGO_FREE_TEST"
fi

# Test 7: Unit tests
print_step "Unit Tests (test_http_client.py)"
cd src
UNIT_TEST_OUTPUT=$(python -m pytest tests/test_http_client.py -v --tb=short 2>&1)
UNIT_TEST_RESULT=$?
cd ..

if [ $UNIT_TEST_RESULT -eq 0 ]; then
    PASSED_COUNT=$(echo "$UNIT_TEST_OUTPUT" | grep -o "[0-9]* passed" | grep -o "[0-9]*")
    print_success "All $PASSED_COUNT unit tests passed"
else
    print_error "Unit tests failed"
    echo "$UNIT_TEST_OUTPUT" | grep -E "(FAILED|ERROR)" | sed 's/^/      /'
fi

# Test 8: Integration tests
print_step "Integration Tests (test_good_citizen_integration.py)"
cd src
INTEGRATION_TEST_OUTPUT=$(python -m pytest tests/test_good_citizen_integration.py -v --tb=short 2>&1)
INTEGRATION_TEST_RESULT=$?
cd ..

if [ $INTEGRATION_TEST_RESULT -eq 0 ]; then
    PASSED_COUNT=$(echo "$INTEGRATION_TEST_OUTPUT" | grep -o "[0-9]* passed" | grep -o "[0-9]*")
    print_success "All $PASSED_COUNT integration tests passed"
else
    print_error "Integration tests failed"
    echo "$INTEGRATION_TEST_OUTPUT" | grep -E "(FAILED|ERROR)" | sed 's/^/      /'
fi

# Test 9: Collector loading (without Django setup)
print_step "Collector Loading (Django-free mode)"
COLLECTOR_TEST=$(python -c "
import sys
import os
import asyncio

# Don't configure Django
os.environ.pop('DJANGO_SETTINGS_MODULE', None)

sys.path.insert(0, 'src')

async def test():
    try:
        from crawler_api.surfaces.registry import get_collector
        from shared.http_client import RateLimitedClient

        # Test that we can load a collector
        collector = get_collector('crawler_api.surfaces.hackernews:collect')

        print('OK')
    except Exception as e:
        print(f'ERROR: {e}')

asyncio.run(test())
" 2>&1)

if echo "$COLLECTOR_TEST" | grep -q "OK"; then
    print_success "Collectors can be loaded without Django setup"
else
    print_error "Collector loading failed: $COLLECTOR_TEST"
fi

# Test 10: Management command
print_step "Management Command (domain_report)"
DOMAIN_REPORT_OUTPUT=$(python manage.py domain_report --help 2>&1)
if echo "$DOMAIN_REPORT_OUTPUT" | grep -q "Display domain health and metrics report"; then
    print_success "domain_report command available"
else
    print_error "domain_report command not found"
fi

# Test 11: Collector updates verification
print_step "Collector RateLimitedClient Integration"
COLLECTORS=(
    "crawler_api/surfaces/hackernews.py"
    "crawler_api/surfaces/reddit_hot.py"
    "crawler_api/surfaces/base_rss.py"
    "crawler_api/surfaces/youtube_trending.py"
    "crawler_api/surfaces/twitter_trending.py"
    "crawler_api/surfaces/wenxuecity_news.py"
)

ALL_COLLECTORS_UPDATED=true
for collector in "${COLLECTORS[@]}"; do
    if grep -q "from shared.http_client import RateLimitedClient" "src/$collector" 2>/dev/null; then
        continue
    else
        print_warning "$(basename $collector) not using RateLimitedClient"
        ALL_COLLECTORS_UPDATED=false
    fi
done

if [ "$ALL_COLLECTORS_UPDATED" = true ]; then
    print_success "All 6 collectors use RateLimitedClient"
else
    print_error "Some collectors not updated"
fi

# Test 12: Worker updates verification
print_step "Worker Circuit Breaker Integration"
if grep -q "CircuitBreakerOpen" "src/crawler_api/workers/surface_worker.py" 2>/dev/null; then
    print_success "surface_worker.py handles CircuitBreakerOpen exception"
else
    print_error "surface_worker.py missing CircuitBreakerOpen handling"
fi

if grep -q "jittered_sleep" "src/crawler_api/workers/surface_worker.py" 2>/dev/null; then
    print_success "surface_worker.py uses jittered sleep"
else
    print_warning "surface_worker.py not using jittered sleep"
fi

# Test 13: Documentation check
print_step "Documentation"
if [ -f "src/shared/http_client.py" ]; then
    DOCSTRING_COUNT=$(grep -c '"""' src/shared/http_client.py || echo "0")
    if [ "$DOCSTRING_COUNT" -gt 10 ]; then
        print_success "http_client.py is well-documented ($DOCSTRING_COUNT docstrings)"
    else
        print_warning "Limited documentation in http_client.py"
    fi
else
    print_error "http_client.py not found"
fi

# Print summary
print_summary
exit $?
