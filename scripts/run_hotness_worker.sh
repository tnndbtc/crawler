#!/bin/bash
#
# Hotness Worker Runner
#
# Computes and updates hotness scores for trend items.
#
# From REQUIREMENTS-MASTER.md:
# - Computes hotness scores for new items (hotness=NULL)
# - Recomputes scores for recent items (<48h old)
# - Uses time decay and engagement metrics
# - Never blocks collection (runs separately)
#
# Usage:
#   ./scripts/run_hotness_worker.sh
#
# Environment variables (from .env):
#   HOTNESS_WORKER_POLL_INTERVAL=300   # Seconds between polls (default: 5 min)
#   HOTNESS_BATCH_SIZE=100             # Items to process per batch
#   HOTNESS_BACKFILL_MODE=normal       # 'normal' or 'aggressive'
#   LOG_LEVEL=INFO                     # Logging level
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
echo "Hotness Worker Configuration"
echo "========================================="
echo "HOTNESS_WORKER_POLL_INTERVAL: ${HOTNESS_WORKER_POLL_INTERVAL:-300}"
echo "HOTNESS_BATCH_SIZE: ${HOTNESS_BATCH_SIZE:-100}"
echo "HOTNESS_BACKFILL_MODE: ${HOTNESS_BACKFILL_MODE:-normal}"
echo "LOG_LEVEL: ${LOG_LEVEL:-INFO}"
echo "========================================="
echo ""

# Ensure logs directory exists
mkdir -p "$PROJECT_ROOT/logs"

# Run worker with logging
echo "Starting hotness worker..."
echo "Logs: $PROJECT_ROOT/logs/hotness_worker.log"
python src/crawler_api/workers/hotness_worker.py 2>&1 | tee -a "$PROJECT_ROOT/logs/hotness_worker.log"
