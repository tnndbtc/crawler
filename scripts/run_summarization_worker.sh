#!/bin/bash
#
# Summarization Worker Runner
#
# Async worker that generates Claude AI summaries for collected trend items.
# Repurposes description_original with a 2-3 sentence summary of what the
# article/post actually says, using claude -p.
#
# Usage:
#   ./scripts/run_summarization_worker.sh
#
# Environment variables (from .env):
#   SUMMARIZATION_WORKER_POLL_INTERVAL=60   # Seconds between polls (default 60)
#   LOG_LEVEL=INFO                          # Logging level
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

# Check Claude CLI is available
claude --version 2>/dev/null || {
    echo "Warning: Claude CLI not found or not authenticated"
    echo "Install with: npm install -g @anthropic-ai/claude-code"
    echo "Auth with:    claude auth"
}

# Display configuration
echo "========================================="
echo "Summarization Worker Configuration"
echo "========================================="
echo "SUMMARIZATION_WORKER_POLL_INTERVAL: ${SUMMARIZATION_WORKER_POLL_INTERVAL:-60}"
echo "LOG_LEVEL: ${LOG_LEVEL:-INFO}"
echo "========================================="
echo ""

# Ensure logs directory exists
mkdir -p "$PROJECT_ROOT/logs"

# Run worker with logging
echo "Starting summarization worker..."
echo "Logs: $PROJECT_ROOT/logs/summarization_worker.log"
python src/crawler_api/workers/summarization_worker.py 2>&1 | tee -a "$PROJECT_ROOT/logs/summarization_worker.log"
