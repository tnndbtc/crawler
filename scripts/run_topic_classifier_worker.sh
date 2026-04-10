#!/bin/bash
#
# Topic Classifier Worker Runner
#
# Classifies TrendItems by topic (politics, finance, ai, tech, etc.)
# for use by story_engine story selection.
# Two passes: heuristic (fast, free) then LLM (top N% by hotness only, Phase 4).
#
# Usage:
#   ./scripts/run_topic_classifier_worker.sh
#
# Environment variables (from .env):
#   ENABLE_TOPIC_CLASSIFIER=true              # Enable/disable this worker
#   ENABLE_TOPIC_LLM=false                    # Enable LLM pass (Phase 4, off by default)
#   TOPIC_CLASSIFIER_POLL_INTERVAL=600        # Seconds between polls (default 600)
#   LOG_LEVEL=INFO                            # Logging level
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

# Load environment variables (only if not already set)
if [ -f .env ]; then
    echo "Loading environment from .env (preserving existing env vars)"
    while IFS= read -r line; do
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ -z "$line" ]] && continue
        key="${line%%=*}"
        value="${line#*=}"
        value="${value%%#*}"
        value="${value%"${value##*[![:space:]]}"}"
        if [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] && [ -z "${!key:-}" ]; then
            export "$key=$value"
        fi
    done < <(grep -E '^[A-Z_][A-Z0-9_]*=' .env)
fi

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Check Django can be imported
python -c "import django" 2>/dev/null || {
    echo "Error: Django not installed. Run: pip install -r requirements.txt"
    exit 1
}

# Display configuration
echo "========================================="
echo "Topic Classifier Worker Configuration"
echo "========================================="
echo "ENABLE_TOPIC_LLM:                ${ENABLE_TOPIC_LLM:-false}"
echo "TOPIC_CLASSIFIER_POLL_INTERVAL:  ${TOPIC_CLASSIFIER_POLL_INTERVAL:-600}"
echo "LOG_LEVEL:                       ${LOG_LEVEL:-INFO}"
echo "========================================="
echo ""

# Ensure logs directory exists
mkdir -p "$PROJECT_ROOT/logs"

echo "Starting topic classifier worker..."
echo "Logs: $PROJECT_ROOT/logs/topic_classifier_worker.log"
python src/crawler_api/workers/topic_classifier_worker.py 2>&1 | tee -a "$PROJECT_ROOT/logs/topic_classifier_worker.log"
