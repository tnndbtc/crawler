#!/bin/bash
#
# Translation Worker Runner
#
# Async translation worker with canonical-first priority.
#
# From REQUIREMENTS-MASTER.md:
# - Creates en-US translations for non-English items
# - Processes pending translations using DeepL or OpenAI
# - Never blocks collection (runs separately)
#
# Usage:
#   ./scripts/run_translation_worker.sh
#
# Environment variables (from .env):
#   DEEPL_API_KEY=...                     # DeepL API key (recommended)
#   OPENAI_API_KEY=...                    # OpenAI API key (fallback)
#   TRANSLATION_WORKER_POLL_INTERVAL=30   # Seconds between polls
#   LOG_LEVEL=INFO                        # Logging level
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

# Check API keys
if [ -z "$DEEPL_API_KEY" ] && [ -z "$OPENAI_API_KEY" ]; then
    echo "Warning: No translation API keys configured"
    echo "Set DEEPL_API_KEY or OPENAI_API_KEY in .env"
fi

# Display configuration
echo "========================================="
echo "Translation Worker Configuration"
echo "========================================="
echo "DEEPL_API_KEY: ${DEEPL_API_KEY:+***configured***}"
echo "OPENAI_API_KEY: ${OPENAI_API_KEY:+***configured***}"
echo "TRANSLATION_WORKER_POLL_INTERVAL: ${TRANSLATION_WORKER_POLL_INTERVAL:-30}"
echo "LOG_LEVEL: ${LOG_LEVEL:-INFO}"
echo "========================================="
echo ""

# Run worker
echo "Starting translation worker..."
python src/crawler_api/workers/translation_worker.py
