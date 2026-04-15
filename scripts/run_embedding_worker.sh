#!/bin/bash
#
# Embedding Worker Runner
#
# Generates semantic embedding vectors for crawled trend items using a
# local sentence-transformer model (BAAI/bge-small-en-v1.5, 384 dims).
#
# Two phases per run:
#   1. BACKFILL  — embeds all existing items with canonical_title (~14 min
#                  for 10 K items on 4 CPUs; runs once then stops)
#   2. ONGOING   — polls every EMBEDDING_WORKER_POLL_INTERVAL seconds for
#                  newly translated items and embeds them incrementally.
#
# Vectors are stored in the ItemDerivation table (derivation_type='embedding')
# and consumed by the story_engine search functions.
#
# Usage:
#   ./scripts/run_embedding_worker.sh
#
# Environment variables (from .env):
#   EMBEDDING_WORKER_POLL_INTERVAL=120   # Seconds between ongoing polls (default 120)
#   EMBEDDING_WORKER_BACKFILL_BATCH=200  # Items per backfill batch (default 200)
#   EMBEDDING_WORKER_ENCODE_BATCH=64     # Items fed to the model at once (default 64)
#   LOG_LEVEL=INFO                       # Logging level
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

# Check sentence-transformers is available
python -c "import sentence_transformers" 2>/dev/null || {
    echo "Error: sentence-transformers not installed."
    echo "Run: pip install sentence-transformers"
    exit 1
}

# Display configuration
echo "========================================="
echo "Embedding Worker Configuration"
echo "========================================="
echo "Model:                           BAAI/bge-small-en-v1.5 (384 dims)"
echo "EMBEDDING_WORKER_POLL_INTERVAL:  ${EMBEDDING_WORKER_POLL_INTERVAL:-120}s"
echo "EMBEDDING_WORKER_BACKFILL_BATCH: ${EMBEDDING_WORKER_BACKFILL_BATCH:-200}"
echo "EMBEDDING_WORKER_ENCODE_BATCH:   ${EMBEDDING_WORKER_ENCODE_BATCH:-64}"
echo "LOG_LEVEL:                       ${LOG_LEVEL:-INFO}"
echo "========================================="
echo ""

# Ensure logs directory exists
mkdir -p "$PROJECT_ROOT/logs"

# Run worker with logging
echo "Starting embedding worker..."
echo "Logs: $PROJECT_ROOT/logs/embedding_worker.log"
python src/crawler_api/workers/embedding_worker.py 2>&1 | tee -a "$PROJECT_ROOT/logs/embedding_worker.log"
