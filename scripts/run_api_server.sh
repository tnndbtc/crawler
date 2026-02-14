#!/bin/bash
#
# FastAPI Server Runner
#
# Starts the FastAPI read-only API for trend data consumption.
#
# From REQUIREMENTS-MASTER.md:
# - Read-only endpoints for regions, surfaces, trends
# - Observability endpoints for health monitoring
# - canonical_title in responses
#
# Usage:
#   ./scripts/run_api_server.sh
#
# Environment variables (from .env):
#   API_HOST=0.0.0.0          # Host to bind to
#   API_PORT=8000             # Port to listen on
#   LOG_LEVEL=INFO            # Logging level
#

set -e  # Exit on error

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Change to project root
cd "$PROJECT_ROOT"

# Load environment variables
if [ -f .env ]; then
    echo "Loading environment from .env"
    export $(grep -v '^#' .env | xargs)
fi

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    echo "Activating virtual environment"
    source venv/bin/activate
elif [ -d ".venv" ]; then
    echo "Activating virtual environment"
    source .venv/bin/activate
fi

# Check FastAPI can be imported
python -c "import fastapi" 2>/dev/null || {
    echo "Error: FastAPI not installed. Run: pip install -r requirements.txt"
    exit 1
}

# Configuration
API_HOST=${API_HOST:-0.0.0.0}
API_PORT=${API_PORT:-8000}
LOG_LEVEL=${LOG_LEVEL:-info}

# Display configuration
echo "========================================="
echo "API Server Configuration"
echo "========================================="
echo "API_HOST: $API_HOST"
echo "API_PORT: $API_PORT"
echo "LOG_LEVEL: $LOG_LEVEL"
echo "========================================="
echo ""
echo "API will be available at: http://$API_HOST:$API_PORT"
echo "API docs: http://$API_HOST:$API_PORT/docs"
echo ""

# Run server
echo "Starting API server..."
uvicorn crawler_api.main:app \
    --host $API_HOST \
    --port $API_PORT \
    --log-level $LOG_LEVEL
