#!/bin/bash
#
# Surface Worker Runner
#
# Executes trend surface collectors with full observability.
#
# From REQUIREMENTS-MASTER.md:
# - Polls for due surfaces
# - Creates CrawlRun audit logs
# - Respects DRY_RUN mode for testing
# - Enforces MAX_RUN_SECONDS timeout
# - Updates surface health fields
#
# Usage:
#   ./scripts/run_surface_worker.sh
#
# Environment variables (from .env):
#   DRY_RUN=false                    # Set to 'true' for testing
#   MAX_RUN_SECONDS=60               # Timeout per surface
#   SURFACE_WORKER_POLL_INTERVAL=60  # Seconds between polls
#   LOG_LEVEL=INFO                   # Logging level
#

set -e  # Exit on error

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Change to project root
cd "$PROJECT_ROOT"

# Load environment variables (only if not already set)
if [ -f .env ]; then
    echo "Loading environment from .env (preserving existing env vars)"
    while IFS= read -r line; do
        # Skip comments and empty lines
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ -z "$line" ]] && continue

        # Extract key (everything before first =)
        key="${line%%=*}"
        # Extract value (everything after first =, strip trailing comments)
        value="${line#*=}"
        value="${value%%#*}"        # Remove trailing comments
        value="${value%"${value##*[![:space:]]}"}"  # Trim trailing whitespace

        # Only set if key is valid and not already defined in environment
        if [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] && [ -z "${!key:-}" ]; then
            export "$key=$value"
        fi
    done < <(grep -E '^[A-Z_][A-Z0-9_]*=' .env)
fi

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    echo "Activating virtual environment"
    source venv/bin/activate
elif [ -d ".venv" ]; then
    echo "Activating virtual environment"
    source .venv/bin/activate
fi

# Check Django can be imported
python -c "import django" 2>/dev/null || {
    echo "Error: Django not installed. Run: pip install -r requirements.txt"
    exit 1
}

# Display configuration
echo "========================================="
echo "Surface Worker Configuration"
echo "========================================="
echo "DRY_RUN: ${DRY_RUN:-false}"
echo "MAX_RUN_SECONDS: ${MAX_RUN_SECONDS:-60}"
echo "SURFACE_WORKER_POLL_INTERVAL: ${SURFACE_WORKER_POLL_INTERVAL:-60}"
echo "LOG_LEVEL: ${LOG_LEVEL:-INFO}"
echo "========================================="
echo ""

# Run worker
echo "Starting surface worker..."
python src/crawler_api/workers/surface_worker.py
