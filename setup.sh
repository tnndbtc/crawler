#!/bin/bash

################################################################################
# Trend Crawler Setup & Management Script
#
# User-friendly script for managing the Culture-Flexible Trend Crawler
# Designed for users at all skill levels
################################################################################

set -e  # Exit on error (disabled for menu loop)

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Directory paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_DIR="${SCRIPT_DIR}/.pids"
LOG_DIR="${SCRIPT_DIR}/logs"
BACKUP_DIR="${SCRIPT_DIR}/backups"
ENV_FILE="${SCRIPT_DIR}/.env"
DB_FILE="${SCRIPT_DIR}/db.sqlite3"

# Load .env (only set vars that aren't already in environment)
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

# Service configuration
DJANGO_PORT=8001
API_PORT=8002

################################################################################
# Helper Functions
################################################################################

print_header() {
    echo -e "\n${BOLD}${CYAN}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}${CYAN}  $1${NC}"
    echo -e "${BOLD}${CYAN}═══════════════════════════════════════════════════════════${NC}\n"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

print_step() {
    echo -e "${CYAN}→${NC} $1"
}

confirm_action() {
    local message="$1"
    echo -e "${YELLOW}⚠${NC} ${message}"
    read -p "Continue? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_info "Operation cancelled"
        return 1
    fi
    return 0
}

# Create necessary directories
ensure_directories() {
    mkdir -p "$PID_DIR" "$LOG_DIR" "$BACKUP_DIR"
}

# Check if a service is running (PID file first, then actual process check)
is_service_running() {
    local service_name="$1"
    local pid_file="${PID_DIR}/${service_name}.pid"

    # Check PID file first
    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        if ps -p "$pid" > /dev/null 2>&1; then
            return 0  # Running
        else
            # Stale PID file
            rm -f "$pid_file"
        fi
    fi

    # Fallback: check actual process regardless of PID file
    local process_pid=""
    case "$service_name" in
        django_admin)
            process_pid=$(ps aux | grep "manage.py runserver" | grep -v grep | head -1 | awk '{print $2}')
            ;;
        api_server)
            process_pid=$(ps aux | grep "uvicorn.*crawler_api" | grep -v grep | head -1 | awk '{print $2}')
            ;;
        translation_worker)
            process_pid=$(ps aux | grep "python.*translation_worker\.py" | grep -v grep | head -1 | awk '{print $2}')
            ;;
        surface_worker)
            process_pid=$(ps aux | grep "python.*surface_worker\.py" | grep -v grep | head -1 | awk '{print $2}')
            ;;
        hotness_worker)
            process_pid=$(ps aux | grep "python.*hotness_worker\.py" | grep -v grep | head -1 | awk '{print $2}')
            ;;
        summarization_worker)
            process_pid=$(ps aux | grep "python.*summarization_worker\.py" | grep -v grep | head -1 | awk '{print $2}')
            ;;
    esac

    if [ ! -z "$process_pid" ]; then
        return 0  # Running
    fi

    return 1  # Not running
}

# Check if port is in use
is_port_in_use() {
    local port="$1"
    if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1 ; then
        return 0  # Port in use
    fi
    return 1  # Port free
}

# Get PID for a service (PID file first, then actual process)
get_service_pid() {
    local service_name="$1"
    local pid_file="${PID_DIR}/${service_name}.pid"

    # Check PID file first
    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        if ps -p "$pid" > /dev/null 2>&1; then
            echo "$pid"
            return
        fi
    fi

    # Fallback: find actual process
    case "$service_name" in
        django_admin)
            ps aux | grep "manage.py runserver" | grep -v grep | head -1 | awk '{print $2}'
            ;;
        api_server)
            ps aux | grep "uvicorn.*crawler_api" | grep -v grep | head -1 | awk '{print $2}'
            ;;
        translation_worker)
            ps aux | grep "python.*translation_worker\.py" | grep -v grep | head -1 | awk '{print $2}'
            ;;
        surface_worker)
            ps aux | grep "python.*surface_worker\.py" | grep -v grep | head -1 | awk '{print $2}'
            ;;
        hotness_worker)
            ps aux | grep "python.*hotness_worker\.py" | grep -v grep | head -1 | awk '{print $2}'
            ;;
        summarization_worker)
            ps aux | grep "python.*summarization_worker\.py" | grep -v grep | head -1 | awk '{print $2}'
            ;;
        *)
            echo ""
            ;;
    esac
}

################################################################################
# System Requirements Check
################################################################################

check_requirements() {
    print_header "System Requirements Check"

    local all_ok=true

    # Check Python version
    print_step "Checking Python version..."
    if command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 --version | awk '{print $2}')
        PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
        PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)

        if [ "$PYTHON_MAJOR" -ge 3 ] && [ "$PYTHON_MINOR" -ge 8 ]; then
            print_success "Python $PYTHON_VERSION (requirement: 3.8+)"
        else
            print_error "Python $PYTHON_VERSION found, but 3.8+ required"
            all_ok=false
        fi
    else
        print_error "Python 3 not found"
        all_ok=false
    fi

    # Check pip
    print_step "Checking pip..."
    if command -v pip3 &> /dev/null || command -v pip &> /dev/null; then
        print_success "pip is installed"
    else
        print_error "pip not found"
        all_ok=false
    fi

    # Check virtual environment
    print_step "Checking for virtual environment..."
    if [ -n "$VIRTUAL_ENV" ]; then
        print_success "Virtual environment active: $VIRTUAL_ENV"
    else
        print_warning "No virtual environment detected"
        print_info "Consider activating a venv: python3 -m venv venv && source venv/bin/activate"
    fi

    # Check .env file
    print_step "Checking .env configuration..."
    if [ -f "$ENV_FILE" ]; then
        print_success ".env file exists"
    else
        if [ -f "${SCRIPT_DIR}/.env.example" ]; then
            print_info ".env file not found (will be auto-created from .env.example on start)"
        else
            print_error ".env.example not found"
            all_ok=false
        fi
    fi

    # Check for required API keys (from environment or .env)
    print_step "Checking API keys..."
    local api_vars=("DEEPL_API_KEY" "OPENAI_API_KEY")
    for var in "${api_vars[@]}"; do
        # Check environment variable first
        local env_value=$(printenv "$var" 2>/dev/null)
        if [ -n "$env_value" ] && [[ ! "$env_value" =~ ^your- ]]; then
            print_success "  $var is set (from environment)"
        elif [ -f "$ENV_FILE" ] && grep -q "^${var}=" "$ENV_FILE" && ! grep -q "^${var}=your-" "$ENV_FILE"; then
            print_success "  $var is set (from .env)"
        else
            print_warning "  $var needs configuration (set in environment or .env)"
        fi
    done

    # Check database
    print_step "Checking database..."
    if [ -f "$DB_FILE" ]; then
        DB_SIZE=$(du -h "$DB_FILE" | cut -f1)
        print_success "Database exists (size: $DB_SIZE)"
    else
        print_warning "Database not initialized"
        print_info "Run option 8 (First-Time Setup) to create it"
    fi

    # Check required Python packages
    print_step "Checking Python dependencies..."
    if python3 -c "import django, fastapi, uvicorn" 2>/dev/null; then
        print_success "Core Python packages installed"
    else
        print_warning "Some Python packages missing"
        print_info "Run option 9 (Update Dependencies) to install them"
    fi

    echo ""
    if [ "$all_ok" = true ]; then
        print_success "All critical requirements met!"
    else
        print_error "Some requirements not met. Please address the issues above."
    fi

    echo ""
}

################################################################################
# Service Management
################################################################################

start_service() {
    local service_name="$1"
    local service_description="$2"
    local start_command="$3"

    local pid_file="${PID_DIR}/${service_name}.pid"
    local log_file="${LOG_DIR}/${service_name}.log"

    if is_service_running "$service_name"; then
        print_warning "$service_description is already running (PID: $(get_service_pid $service_name))"
        return 0
    fi

    print_step "Starting $service_description..."
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] === Starting $service_description ===" >> "$log_file"

    # Start service in background with nohup
    # Pass API keys from current environment to child process
    cd "$SCRIPT_DIR"
    nohup env \
        OPENAI_API_KEY="${OPENAI_API_KEY:-}" \
        DEEPL_API_KEY="${DEEPL_API_KEY:-}" \
        bash -c "$start_command" >> "$log_file" 2>&1 &
    local pid=$!

    # Save PID
    echo $pid > "$pid_file"

    # Wait a moment and check if it's still running
    sleep 2
    if ps -p $pid > /dev/null 2>&1; then
        print_success "$service_description started (PID: $pid)"
        print_info "  Logs: $log_file"
        return 0
    else
        print_error "$service_description failed to start"
        print_info "Check logs: tail -f $log_file"
        rm -f "$pid_file"
        return 1
    fi
}

stop_service() {
    local service_name="$1"
    local service_description="$2"

    local pid_file="${PID_DIR}/${service_name}.pid"

    if ! is_service_running "$service_name"; then
        print_info "$service_description is not running"
        rm -f "$pid_file"
        return 0
    fi

    # Find ALL pids for this service (python worker, bash wrapper, tee)
    local all_pids=""
    case "$service_name" in
        django_admin)
            all_pids=$(ps aux | grep "manage.py runserver" | grep -v grep | awk '{print $2}')
            ;;
        api_server)
            all_pids=$(ps aux | grep "uvicorn.*crawler_api" | grep -v grep | awk '{print $2}')
            ;;
        translation_worker)
            all_pids=$(ps aux | grep -E "translation_worker\.(py|sh)|tee.*translation_worker" | grep -v grep | awk '{print $2}')
            ;;
        surface_worker)
            all_pids=$(ps aux | grep -E "surface_worker\.(py|sh)|tee.*surface_worker" | grep -v grep | awk '{print $2}')
            ;;
        hotness_worker)
            all_pids=$(ps aux | grep -E "hotness_worker\.(py|sh)|tee.*hotness_worker" | grep -v grep | awk '{print $2}')
            ;;
        summarization_worker)
            all_pids=$(ps aux | grep -E "summarization_worker\.(py|sh)|tee.*summarization_worker" | grep -v grep | awk '{print $2}')
            ;;
    esac

    # Also include PID file pid
    if [ -f "$pid_file" ]; then
        local file_pid=$(cat "$pid_file")
        all_pids="$file_pid $all_pids"
    fi

    # Deduplicate
    all_pids=$(echo "$all_pids" | tr ' ' '\n' | sort -u | tr '\n' ' ')

    print_step "Stopping $service_description (PIDs: $all_pids)..."
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] === Stopping $service_description (PIDs: $all_pids) ===" >> "$log_file"

    # Try graceful shutdown first
    for pid in $all_pids; do
        kill $pid 2>/dev/null || true
    done

    # Wait up to 10 seconds for graceful shutdown
    local still_running=false
    for i in {1..10}; do
        still_running=false
        for pid in $all_pids; do
            if ps -p $pid > /dev/null 2>&1; then
                still_running=true
                break
            fi
        done
        if [ "$still_running" = false ]; then
            rm -f "$pid_file"
            print_success "$service_description stopped"
            return 0
        fi
        sleep 1
    done

    # Force kill if still running
    print_warning "Forcing shutdown..."
    for pid in $all_pids; do
        kill -9 $pid 2>/dev/null || true
    done
    rm -f "$pid_file"
    print_success "$service_description stopped (forced)"
}

start_all_services() {
    print_header "Starting All Services"

    ensure_directories

    # Auto-install dependencies
    if [ -f "${SCRIPT_DIR}/requirements.txt" ]; then
        print_step "Checking dependencies..."
        pip install -q -r "${SCRIPT_DIR}/requirements.txt" 2>&1 | grep -v "already satisfied" | head -5
        print_success "Dependencies OK"
    fi

    # Auto-create .env from .env.example if it doesn't exist
    # This is safe because critical values (API keys) come from environment variables
    if [ ! -f "$ENV_FILE" ]; then
        if [ -f "${SCRIPT_DIR}/.env.example" ]; then
            cp "${SCRIPT_DIR}/.env.example" "$ENV_FILE"
            print_info ".env file auto-created from .env.example"
            print_info "Note: API keys should be set via environment variables"
        else
            print_error ".env.example not found - cannot create .env"
            return 1
        fi
    fi

    # Check database - auto-run migrations if not exists
    if [ ! -f "$DB_FILE" ]; then
        print_warning "Database not found - running migrations..."
        cd "$SCRIPT_DIR"
        python3 manage.py migrate 2>&1 | grep -v "virtualenvwrapper" | grep -v "hook_loader" | grep -v "ModuleNotFoundError"
        if [ ! -f "$DB_FILE" ]; then
            print_error "Database creation failed"
            return 1
        fi
        print_success "Database created"
    fi

    # Auto-seed from config/seeds.json (idempotent — safe to run every start)
    print_step "Loading seeds from config/seeds.json..."
    cd "$SCRIPT_DIR"
    python3 -c "
import os, sys, django
sys.path.insert(0, 'src')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()
from shared.seed_loader import load_seeds
load_seeds()
" 2>&1 | grep -v "virtualenvwrapper" | grep -v "hook_loader" | grep -v "ModuleNotFoundError"
    print_success "Seeds loaded"

    # Check ports
    if is_port_in_use $DJANGO_PORT; then
        print_warning "Port $DJANGO_PORT is already in use"
    fi

    if is_port_in_use $API_PORT; then
        print_warning "Port $API_PORT is already in use"
    fi

    echo ""

    # Start services
    start_service "django_admin" "Django Admin Server" \
        "python3 manage.py runserver 0.0.0.0:$DJANGO_PORT"

    start_service "api_server" "FastAPI Server" \
        "python3 -m uvicorn src.crawler_api.main:app --host 0.0.0.0 --port $API_PORT"

    start_service "surface_worker" "Surface Worker" \
        "${SCRIPT_DIR}/scripts/run_surface_worker.sh"

    if [ "${ENABLE_TRANSLATION_WORKER:-true}" = "true" ]; then
        start_service "translation_worker" "Translation Worker" \
            "${SCRIPT_DIR}/scripts/run_translation_worker.sh"
    else
        print_info "Translation Worker: DISABLED (ENABLE_TRANSLATION_WORKER=false in .env)"
    fi

    start_service "hotness_worker" "Hotness Worker" \
        "${SCRIPT_DIR}/scripts/run_hotness_worker.sh"

    if [ "${ENABLE_SUMMARIZATION_WORKER:-true}" = "true" ]; then
        start_service "summarization_worker" "Summarization Worker" \
            "${SCRIPT_DIR}/scripts/run_summarization_worker.sh"
    else
        print_info "Summarization Worker: DISABLED (ENABLE_SUMMARIZATION_WORKER=false in .env)"
    fi

    echo ""
    print_success "All services started!"
    print_info "Use option 5 to check service status"
    print_info "Use option 6 to view access URLs"
    echo ""
}

stop_all_services() {
    print_header "Stopping All Services"

    # Step 1: Stop tracked services (original behavior)
    print_step "Stopping tracked services..."
    stop_service "summarization_worker" "Summarization Worker"
    stop_service "hotness_worker" "Hotness Worker"
    stop_service "translation_worker" "Translation Worker"
    stop_service "surface_worker" "Surface Worker"
    stop_service "api_server" "FastAPI Server"
    stop_service "django_admin" "Django Admin Server"

    # Step 2: Kill any remaining worker processes by pattern
    print_step "Checking for untracked worker processes..."

    # Find ALL Python worker processes (scripts and actual workers)
    local worker_pids=$(ps aux | grep -E "(translation_worker\.py|surface_worker\.py|hotness_worker\.py|run_.*_worker\.sh)" | grep -v grep | awk '{print $2}')

    if [ ! -z "$worker_pids" ]; then
        print_warning "Found untracked worker processes, stopping them..."
        echo "$worker_pids" | while read pid; do
            if [ ! -z "$pid" ]; then
                print_info "  Killing process $pid"
                kill $pid 2>/dev/null || kill -9 $pid 2>/dev/null
            fi
        done
        sleep 1
    else
        print_info "No untracked workers found"
    fi

    # Step 3: Kill processes using our ports
    print_step "Checking for processes using crawler ports..."

    for port in $DJANGO_PORT $API_PORT; do
        if is_port_in_use $port; then
            local port_pid=$(lsof -ti :$port 2>/dev/null)
            if [ ! -z "$port_pid" ]; then
                print_warning "Port $port is still in use (PID: $port_pid), stopping it..."
                kill $port_pid 2>/dev/null || kill -9 $port_pid 2>/dev/null
                sleep 1
            fi
        fi
    done

    # Step 4: Clean up any stale PID files
    print_step "Cleaning up PID files..."
    if [ -d "$PID_DIR" ]; then
        rm -f "$PID_DIR"/*.pid
        print_info "PID files cleaned"
    fi

    echo ""
    print_success "All services stopped (including untracked processes)"

    # Verify nothing is running
    local remaining=$(ps aux | grep -E "(runserver|uvicorn|run_.*_worker)" | grep -v grep | wc -l)
    if [ $remaining -eq 0 ]; then
        print_success "Verified: No crawler processes running"
    else
        print_warning "Some processes may still be running. Check with option 5 (Service Status)"
    fi

    echo ""
}

restart_all_services() {
    print_header "Restarting All Services"
    stop_all_services
    echo ""
    sleep 2
    start_all_services
}

start_or_restart_services() {
    # If anything is running, restart. Otherwise just start.
    local any_running=false
    for svc in django_admin api_server surface_worker translation_worker hotness_worker summarization_worker; do
        if is_service_running "$svc"; then
            any_running=true
            break
        fi
    done

    if [ "$any_running" = true ]; then
        restart_all_services
    else
        start_all_services
    fi
}

show_service_status() {
    print_header "Service Status"

    local services=(
        "django_admin:Django Admin Server:$DJANGO_PORT"
        "api_server:FastAPI Server:$API_PORT"
        "surface_worker:Surface Worker:-"
        "translation_worker:Translation Worker:-"
        "hotness_worker:Hotness Worker:-"
        "summarization_worker:Summarization Worker:-"
    )

    echo -e "${BOLD}Service                    Status      PID       Port${NC}"
    echo "─────────────────────────────────────────────────────────"

    for service_info in "${services[@]}"; do
        IFS=':' read -r service_name service_desc port <<< "$service_info"

        printf "%-26s " "$service_desc"

        if is_service_running "$service_name"; then
            local pid=$(get_service_pid "$service_name")
            printf "${GREEN}%-11s${NC} " "Running"
            printf "%-9s " "$pid"
            if [ "$port" != "-" ]; then
                printf "%s\n" "$port"
            else
                printf "%s\n" "N/A"
            fi
        else
            printf "${RED}%-11s${NC} " "Stopped"
            printf "%-9s " "-"
            printf "%s\n" "-"
        fi
    done

    echo ""

    # Show log files
    print_info "Log files location: $LOG_DIR"
    if [ -d "$LOG_DIR" ] && [ "$(ls -A $LOG_DIR)" ]; then
        for log_file in "$LOG_DIR"/*.log; do
            if [ -f "$log_file" ]; then
                local size=$(du -h "$log_file" | cut -f1)
                echo "  - $(basename $log_file) ($size)"
            fi
        done
    fi

    echo ""
}

################################################################################
# URL Display
################################################################################

show_urls() {
    print_header "Trend Crawler API Documentation"

    # Detect local IP address
    local HOST_IP=$(hostname -I | awk '{print $1}')
    if [ -z "$HOST_IP" ]; then
        # Fallback method
        HOST_IP=$(ip route get 1 2>/dev/null | awk '{print $7; exit}')
    fi
    if [ -z "$HOST_IP" ]; then
        # Final fallback
        HOST_IP="localhost"
        print_warning "Could not detect local IP, using localhost"
    else
        print_info "Server IP: $HOST_IP"
    fi
    echo ""

    # Check if services are actually running
    local services_running=false
    if is_service_running "django_admin" || is_service_running "api_server"; then
        services_running=true
        print_success "Services are running - URLs are accessible"
    else
        print_warning "Services are not running - start them with option 2"
    fi

    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}                        WEB INTERFACES                              ${NC}"
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo ""

    echo -e "${BOLD}Django Admin Interface:${NC}"
    echo -e "  ${CYAN}http://$HOST_IP:$DJANGO_PORT/admin${NC}"
    echo -e "  Configure regions, surfaces, and view collected data"
    echo ""

    echo -e "${BOLD}FastAPI Server:${NC}"
    echo -e "  ${CYAN}http://$HOST_IP:$API_PORT${NC}"
    echo -e "  Main API endpoint"
    echo ""

    echo -e "${BOLD}Interactive API Documentation:${NC}"
    echo -e "  ${CYAN}http://$HOST_IP:$API_PORT/docs${NC} (Swagger UI - Try APIs)"
    echo -e "  ${CYAN}http://$HOST_IP:$API_PORT/redoc${NC} (ReDoc - Read-only)"
    echo ""

    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}                    API QUICK REFERENCE                             ${NC}"
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo ""

    echo -e "${BOLD}Base URL:${NC} ${CYAN}http://$HOST_IP:$API_PORT/api/v1${NC}"
    echo ""

    echo -e "${BOLD}━━━ Main Endpoints ━━━${NC}"
    echo ""
    echo -e "${BOLD}1. GET /trends${NC} - Get trend items (cursor-based pagination)"
    echo -e "   ${CYAN}curl \"http://$HOST_IP:$API_PORT/api/v1/trends?limit=50\"${NC}"
    echo ""
    echo "   Query Parameters:"
    echo "     • cursor       - Pagination cursor (from previous response)"
    echo "     • limit        - Items to return (1-200, default: 50)"
    echo "     • region       - Filter by region key (us, jp, kr, etc.)"
    echo "     • bucket       - Filter by bucket (hot_now, news, etc.)"
    echo ""
    echo "   Response:"
    echo "     {"
    echo "       \"items\": [...],"
    echo "       \"next_cursor\": \"base64-string\","
    echo "       \"has_more\": true"
    echo "     }"
    echo ""

    echo -e "${BOLD}2. GET /regions${NC} - List available regions"
    echo -e "   ${CYAN}curl \"http://$HOST_IP:$API_PORT/api/v1/regions?enabled_only=true\"${NC}"
    echo ""
    echo "   Query Parameters:"
    echo "     • enabled_only - Only show enabled regions (default: true)"
    echo ""
    echo "   Response:"
    echo "     ["
    echo "       {"
    echo "         \"key\": \"us\","
    echo "         \"name\": \"United States\","
    echo "         \"default_locale\": \"en-US\","
    echo "         \"enabled\": true"
    echo "       }"
    echo "     ]"
    echo ""

    echo -e "${BOLD}3. GET /surfaces${NC} - List trend sources"
    echo -e "   ${CYAN}curl \"http://$HOST_IP:$API_PORT/api/v1/surfaces?enabled_only=true&region=us\"${NC}"
    echo ""
    echo "   Query Parameters:"
    echo "     • enabled_only - Only show enabled surfaces (default: true)"
    echo "     • region       - Filter by region key"
    echo ""
    echo "   Response:"
    echo "     ["
    echo "       {"
    echo "         \"id\": 1,"
    echo "         \"region_key\": \"us\","
    echo "         \"platform\": \"google_news\","
    echo "         \"bucket\": \"hot_now\","
    echo "         \"enabled\": true"
    echo "       }"
    echo "     ]"
    echo ""

    echo -e "${BOLD}━━━ Health & Monitoring ━━━${NC}"
    echo ""
    echo -e "${BOLD}4. GET /health${NC} - Basic health check"
    echo -e "   ${CYAN}curl \"http://$HOST_IP:$API_PORT/health\"${NC}"
    echo ""

    echo -e "${BOLD}5. GET /api/v1/health/crawl${NC} - Per-surface crawl status"
    echo -e "   ${CYAN}curl \"http://$HOST_IP:$API_PORT/api/v1/health/crawl\"${NC}"
    echo ""

    echo -e "${BOLD}6. GET /api/v1/health/translation${NC} - Translation queue status"
    echo -e "   ${CYAN}curl \"http://$HOST_IP:$API_PORT/api/v1/health/translation\"${NC}"
    echo ""

    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}                    TREND ITEM SCHEMA                               ${NC}"
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo ""

    cat << 'EOF'
{
  "id": 5422,
  "region_key": "us",
  "platform": "google_news",
  "bucket": "hot_now",

  // Original content (native language)
  "title_original": "Breaking News Title",
  "description_original": "Description text...",
  "original_locale": "en-US",
  "url": "https://...",

  // Canonical (English) content
  "canonical_title": "Translated English Title",
  "canonical_description": "Translated description...",

  // Ranking & engagement
  "rank_position": 1,
  "engagement_signals": {
    "views": 1500000,
    "likes": 85000,
    "comments": 2300
  },

  // Timestamps (ISO 8601)
  "published_at": "2026-02-15T21:01:00Z",
  "collected_at": "2026-02-16T02:55:27.319780Z"
}
EOF

    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}                    USAGE EXAMPLES                                  ${NC}"
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo ""

    echo -e "${BOLD}Example 1: Get first page of trends${NC}"
    echo -e "${CYAN}curl -s \"http://$HOST_IP:$API_PORT/api/v1/trends?limit=50\" | jq .${NC}"
    echo ""

    echo -e "${BOLD}Example 2: Get next page using cursor${NC}"
    echo -e "${CYAN}CURSOR=\$(curl -s \"http://$HOST_IP:$API_PORT/api/v1/trends?limit=50\" | jq -r '.next_cursor')${NC}"
    echo -e "${CYAN}curl -s \"http://$HOST_IP:$API_PORT/api/v1/trends?limit=50&cursor=\$CURSOR\" | jq .${NC}"
    echo ""

    echo -e "${BOLD}Example 3: Filter by region and bucket${NC}"
    echo -e "${CYAN}curl -s \"http://$HOST_IP:$API_PORT/api/v1/trends?limit=50&region=us&bucket=news\" | jq .${NC}"
    echo ""

    echo -e "${BOLD}Example 4: Count items only${NC}"
    echo -e "${CYAN}curl -s \"http://$HOST_IP:$API_PORT/api/v1/trends\" | jq '.items | length'${NC}"
    echo ""

    echo -e "${BOLD}Example 5: Get all regions${NC}"
    echo -e "${CYAN}curl -s \"http://$HOST_IP:$API_PORT/api/v1/regions\" | jq .${NC}"
    echo ""

    echo -e "${BOLD}Example 6: Get surfaces for a region${NC}"
    echo -e "${CYAN}curl -s \"http://$HOST_IP:$API_PORT/api/v1/surfaces?region=us\" | jq .${NC}"
    echo ""

    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}                    IMPORTANT NOTES                                 ${NC}"
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo ""

    echo "  • Cursor is OPAQUE - Never parse or decode it"
    echo "  • When filters change, reset cursor (don't pass it)"
    echo "  • Limit is a HINT - Backend may return fewer items"
    echo "  • All timestamps are in ISO 8601 UTC format"
    echo "  • Canonical fields are always in English"
    echo "  • Deduplication is handled by backend"
    echo "  • CORS enabled for: localhost:3000, 127.0.0.1:3000"
    echo ""

    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════${NC}"
    echo ""

    read -p "Press Enter to continue..." -r
}

################################################################################
# How It Works - Crawler Explanation
################################################################################

show_how_it_works() {
    print_header "How the Crawler Works"

    local doc_file="${SCRIPT_DIR}/docs/HOW-IT-WORKS.txt"

    if [ ! -f "$doc_file" ]; then
        print_error "Documentation file not found: $doc_file"
        return 1
    fi

    # Display with less if available (allows scrolling), otherwise cat
    if command -v less &> /dev/null; then
        less -R "$doc_file"
    else
        cat "$doc_file"
        echo ""
        read -p "Press Enter to continue..." -r
    fi
}

################################################################################
# Log Viewer
################################################################################

view_logs() {
    print_header "Interactive Log Viewer"

    if [ ! -d "$LOG_DIR" ] || [ -z "$(ls -A $LOG_DIR 2>/dev/null)" ]; then
        print_warning "No log files found"
        print_info "Start services first to generate logs"
        return 0
    fi

    echo "Available log files:"
    echo ""

    local log_files=()
    local index=1

    for log_file in "$LOG_DIR"/*.log; do
        if [ -f "$log_file" ]; then
            local basename=$(basename "$log_file")
            local size=$(du -h "$log_file" | cut -f1)
            local lines=$(wc -l < "$log_file")
            echo "  $index) $basename ($size, $lines lines)"
            log_files+=("$log_file")
            ((index++))
        fi
    done

    echo "  a) All logs (merged)"
    echo "  0) Back to main menu"
    echo ""

    read -p "Select log file to view: " choice

    case $choice in
        0)
            return 0
            ;;
        a|A)
            print_info "Viewing all logs (press Ctrl+C to exit)"
            sleep 1
            tail -f "$LOG_DIR"/*.log
            ;;
        [1-9])
            if [ $choice -le ${#log_files[@]} ]; then
                local selected_log="${log_files[$((choice-1))]}"
                print_info "Viewing $(basename $selected_log) (press Ctrl+C to exit)"
                sleep 1
                tail -f "$selected_log"
            else
                print_error "Invalid selection"
            fi
            ;;
        *)
            print_error "Invalid selection"
            ;;
    esac
}

################################################################################
# Database Operations
################################################################################

backup_database() {
    print_header "Database Backup"

    if [ ! -f "$DB_FILE" ]; then
        print_error "Database file not found: $DB_FILE"
        return 1
    fi

    ensure_directories

    local timestamp=$(date +"%Y%m%d_%H%M%S")
    local backup_file="${BACKUP_DIR}/db_backup_${timestamp}.sqlite3"

    print_step "Creating backup..."
    cp "$DB_FILE" "$backup_file"

    local backup_size=$(du -h "$backup_file" | cut -f1)
    print_success "Backup created: $(basename $backup_file) ($backup_size)"
    print_info "Location: $backup_file"

    # Keep only last 10 backups
    print_step "Cleaning old backups (keeping last 10)..."
    local backup_count=$(ls -1 "$BACKUP_DIR"/db_backup_*.sqlite3 2>/dev/null | wc -l)
    if [ $backup_count -gt 10 ]; then
        ls -1t "$BACKUP_DIR"/db_backup_*.sqlite3 | tail -n +11 | xargs rm -f
        print_info "Removed $((backup_count - 10)) old backup(s)"
    fi

    echo ""
}

restore_database() {
    print_header "Database Restore"

    if [ ! -d "$BACKUP_DIR" ] || [ -z "$(ls -A $BACKUP_DIR/db_backup_*.sqlite3 2>/dev/null)" ]; then
        print_error "No backups found in $BACKUP_DIR"
        return 1
    fi

    echo "Available backups:"
    echo ""

    local backups=()
    local index=1

    for backup_file in $(ls -1t "$BACKUP_DIR"/db_backup_*.sqlite3); do
        local basename=$(basename "$backup_file")
        local size=$(du -h "$backup_file" | cut -f1)
        local date_str=$(echo "$basename" | sed 's/db_backup_\(.*\)\.sqlite3/\1/')
        echo "  $index) $basename ($size) - $date_str"
        backups+=("$backup_file")
        ((index++))
    done

    echo "  0) Cancel"
    echo ""

    read -p "Select backup to restore: " choice

    if [ "$choice" = "0" ]; then
        print_info "Restore cancelled"
        return 0
    fi

    if [ $choice -ge 1 ] && [ $choice -le ${#backups[@]} ]; then
        local selected_backup="${backups[$((choice-1))]}"

        echo ""
        confirm_action "This will replace the current database. Current database will be backed up first." || return 1

        # Backup current database first
        if [ -f "$DB_FILE" ]; then
            print_step "Backing up current database..."
            backup_database
        fi

        # Stop services
        print_step "Stopping services..."
        stop_all_services

        # Restore backup
        print_step "Restoring backup..."
        cp "$selected_backup" "$DB_FILE"

        print_success "Database restored from $(basename $selected_backup)"
        print_info "You can now restart the services"
        echo ""
    else
        print_error "Invalid selection"
    fi
}

run_migrations() {
    print_header "Run Database Migrations"

    # Check if database exists
    if [ ! -f "$DB_FILE" ]; then
        print_error "Database not found"
        print_info "Run option 10 (First-Time Setup) first"
        return 1
    fi

    print_info "This will detect model changes, generate migrations, and apply them"
    echo ""

    cd "$SCRIPT_DIR"

    # Step 1: Generate any new migration files from model changes
    print_step "Step 1: Detecting model changes and generating migrations..."
    local makemig_output=$(python3 manage.py makemigrations 2>&1 | grep -v "virtualenvwrapper" | grep -v "hook_loader" | grep -v "ModuleNotFoundError")
    echo "$makemig_output"

    if echo "$makemig_output" | grep -q "No changes detected"; then
        print_success "No new model changes detected"
    else
        print_success "Migration files generated"
    fi
    echo ""

    # Step 2: Check for pending migrations (unapplied)
    print_step "Step 2: Checking for pending migrations..."
    local pending_output=$(python3 manage.py showmigrations --plan 2>&1 | grep -v "virtualenvwrapper" | grep -v "hook_loader" | grep -v "ModuleNotFoundError" | grep "\[ \]")

    if [ -z "$pending_output" ]; then
        print_success "No pending migrations - database is up to date"
        echo ""
    else
        echo "Pending migrations:"
        echo "$pending_output"
        echo ""

        # Stop services for safety
        print_step "Stopping services before applying migrations..."
        stop_all_services

        # Apply migrations
        print_step "Step 3: Applying migrations..."
        python3 manage.py migrate

        if [ $? -ne 0 ]; then
            print_error "Migration failed. Check errors above."
            return 1
        fi
        print_success "Migrations applied successfully"
    fi

    echo ""
}

create_db_user() {
    print_header "Create DB User"

    if [ ! -f "$DB_FILE" ]; then
        print_error "Database not found. Run migrations first."
        return 1
    fi

    cd "$SCRIPT_DIR"

    # Check if any superuser already exists
    existing=$(python3 -c "
import os, sys, django
sys.path.insert(0, 'src')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()
from django.contrib.auth.models import User
print(User.objects.filter(is_superuser=True).count())
" 2>/dev/null)

    if [ "$existing" -gt "0" ]; then
        print_info "There is already $existing superuser(s) in the database."
        echo ""
        echo -e "${BOLD}Create another one anyway? (yes/no):${NC}"
        read -p "> " confirm
        if [ "$confirm" != "yes" ]; then
            print_info "Cancelled."
            echo ""
            return 0
        fi
    fi

    echo ""
    print_step "Creating superuser..."
    print_info "You will be prompted for username, email, and password."
    echo ""
    python3 manage.py createsuperuser

    if [ $? -eq 0 ]; then
        print_success "DB user created successfully!"
    else
        print_error "Failed to create user."
    fi
    echo ""
}

reset_database() {
    print_header "Reset Database"

    echo ""
    print_error "⚠️  WARNING: This will DELETE the database and recreate it from scratch!"
    echo ""
    echo -e "${BOLD}This action will:${NC}"
    echo "  • Delete all collected trend data"
    echo "  • Delete all CrawlRun history"
    echo "  • Delete all translation data"
    echo "  • Remove all surface configurations"
    echo "  • Remove all admin users"
    echo ""
    print_warning "A backup will be created before deletion."
    echo ""
    echo -e "${BOLD}${RED}To confirm, type the database filename 'db.sqlite3' and press Enter:${NC}"
    read -p "> " confirmation

    if [ "$confirmation" != "db.sqlite3" ]; then
        print_info "Database reset cancelled"
        echo ""
        return 1
    fi

    echo ""
    print_step "Confirmation received. Proceeding with database reset..."
    echo ""

    # Backup current database
    if [ -f "$DB_FILE" ]; then
        print_step "Creating backup before reset..."
        backup_database
    fi

    # Stop services
    print_step "Stopping services..."
    stop_all_services

    # Delete database
    print_step "Deleting database..."
    rm -f "$DB_FILE"
    print_success "Database deleted"

    # Run migrations
    print_step "Running migrations..."
    cd "$SCRIPT_DIR"
    python3 manage.py migrate
    print_success "Database recreated"

    # Load seeds from config/seeds.json
    print_step "Loading seeds from config/seeds.json..."
    python3 -c "
import os, sys, django
sys.path.insert(0, 'src')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()
from shared.seed_loader import load_seeds
load_seeds()
" 2>&1 | grep -v "virtualenvwrapper" | grep -v "hook_loader" | grep -v "ModuleNotFoundError"
    print_success "Seeds loaded"

    # Create superuser
    print_step "Creating admin user..."
    print_info "You'll be prompted to create an admin account"
    python3 manage.py createsuperuser

    echo ""
    print_success "Database reset complete!"
    echo ""
}

################################################################################
# Dependency Management
################################################################################

update_dependencies() {
    print_header "Update Dependencies"

    if [ ! -f "${SCRIPT_DIR}/requirements.txt" ]; then
        print_error "requirements.txt not found"
        return 1
    fi

    print_step "Installing/updating Python dependencies..."
    pip install -r "${SCRIPT_DIR}/requirements.txt" --upgrade

    print_success "Dependencies updated"
    print_info "Consider restarting services to use updated packages"
    echo ""
}

################################################################################
# Testing
################################################################################

################################################################################
# Test Helper Functions
################################################################################

# Helper function to run command with timeout
run_with_timeout() {
    local cmd="$1"
    local timeout_sec="$2"
    local output_file=$(mktemp)
    local has_warnings=0

    # Run command and capture output
    timeout $timeout_sec bash -c "$cmd" > "$output_file" 2>&1
    local exit_code=$?

    # Display output
    cat "$output_file"

    # Check for real warnings/failures (not INFO messages about coverage being high)
    # Count real failures but ignore informational messages
    if grep -q "MERGE GATE.*FAIL" "$output_file" 2>/dev/null; then
        has_warnings=1
    fi

    # Also check for ❌ FAIL that are not preceded by INFO
    if grep "❌" "$output_file" 2>/dev/null | grep -v "ℹ️.*INFO" | grep -q "FAIL"; then
        has_warnings=1
    fi

    rm -f "$output_file"
    return $exit_code
}

################################################################################
# Individual Test Functions
################################################################################

run_unit_tests() {
    print_header "Unit Tests (pytest)"
    local timeout_seconds=120

    print_info "Running pytest unit tests with ${timeout_seconds}s timeout..."
    echo ""

    cd "$SCRIPT_DIR"
    if run_with_timeout "python -m pytest src/tests/ -v --tb=short" $timeout_seconds; then
        echo ""
        print_success "✅ Unit Tests: PASSED"
    else
        local exit_code=$?
        echo ""
        if [ $exit_code -eq 124 ]; then
            print_warning "⏱️  Unit Tests: TIMEOUT (exceeded ${timeout_seconds}s)"
        else
            print_error "❌ Unit Tests: FAILED"
        fi
        return 1
    fi
}

run_good_citizen_validation() {
    print_header "Good Citizen Feature Validation"
    local timeout_seconds=120

    if [ ! -f "${SCRIPT_DIR}/scripts/test_good_citizen.sh" ]; then
        print_error "Test script not found: scripts/test_good_citizen.sh"
        return 1
    fi

    print_info "Running Good Citizen validation with ${timeout_seconds}s timeout..."
    print_info "Tests: Rate limiting, Circuit breaker, HTTP caching"
    echo ""

    if run_with_timeout "bash ${SCRIPT_DIR}/scripts/test_good_citizen.sh" $timeout_seconds; then
        echo ""
        print_success "✅ Good Citizen Validation: PASSED"
    else
        local exit_code=$?
        echo ""
        if [ $exit_code -eq 124 ]; then
            print_warning "⏱️  Good Citizen Validation: TIMEOUT (exceeded ${timeout_seconds}s)"
        else
            print_error "❌ Good Citizen Validation: FAILED"
        fi
        return 1
    fi
}

################################################################################
# Run All Tests
################################################################################

run_all_tests() {
    print_header "Run All Tests & Validations"

    echo ""
    print_info "This will run all available test and validation scripts:"
    echo "  1) Unit Tests (pytest)"
    echo "  2) Good Citizen Feature Validation (Rate limiting, Circuit breaker, HTTP caching)"
    echo ""
    print_warning "Each test has a 120-second timeout to prevent hanging"
    echo ""

    local overall_status=0
    local test_results=()
    local timeout_seconds=120

    # Test 1: Unit Tests (pytest)
    print_step "Test 1/2: Running unit tests (pytest, timeout: ${timeout_seconds}s)..."
    cd "$SCRIPT_DIR"
    if run_with_timeout "python -m pytest src/tests/ -v --tb=short" $timeout_seconds; then
        test_results+=("✅ Unit Tests: PASSED")
    else
        local exit_code=$?
        if [ $exit_code -eq 124 ]; then
            test_results+=("⏱️  Unit Tests: TIMEOUT (exceeded ${timeout_seconds}s)")
        else
            test_results+=("❌ Unit Tests: FAILED")
        fi
        overall_status=1
    fi
    echo ""

    # Test 2: Good Citizen Feature Validation
    print_step "Test 2/2: Running Good Citizen feature validation (timeout: ${timeout_seconds}s)..."
    if [ -f "${SCRIPT_DIR}/scripts/test_good_citizen.sh" ]; then
        if run_with_timeout "bash ${SCRIPT_DIR}/scripts/test_good_citizen.sh" $timeout_seconds; then
            test_results+=("✅ Good Citizen Validation: PASSED")
        else
            local exit_code=$?
            if [ $exit_code -eq 124 ]; then
                test_results+=("⏱️  Good Citizen Validation: TIMEOUT (exceeded ${timeout_seconds}s)")
            else
                test_results+=("❌ Good Citizen Validation: FAILED")
            fi
            overall_status=1
        fi
    else
        test_results+=("⚠️  Good Citizen Validation: SKIPPED (script not found)")
    fi
    echo ""

    # Print summary
    echo ""
    print_header "Test Summary"
    echo ""

    for result in "${test_results[@]}"; do
        echo "  $result"
    done

    echo ""
    if [ $overall_status -eq 0 ]; then
        print_success "═══════════════════════════════════════"
        print_success "   ALL TESTS PASSED ✅"
        print_success "═══════════════════════════════════════"
    else
        print_error "═══════════════════════════════════════"
        print_error "   SOME TESTS FAILED ❌"
        print_error "═══════════════════════════════════════"
        print_info "Review the output above for details"
    fi
    echo ""

    return 0  # Always return 0 to avoid exiting setup.sh menu
}

################################################################################
# Test Menu
################################################################################

test_menu() {
    while true; do
        clear
        print_header "Test & Validation Menu"

        echo ""
        echo -e "${BOLD}Run Tests:${NC}"
        echo "  1)  Run All Tests (complete validation suite)"
        echo ""
        echo -e "${BOLD}Individual Tests:${NC}"
        echo "  2)  Unit Tests (pytest)"
        echo "  3)  Good Citizen Feature Validation"
        echo ""
        echo "  0)  Return to Main Menu"
        echo ""
        echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"

        read -p "Select an option: " choice

        case $choice in
            1)
                run_all_tests
                echo ""
                print_info "Press Enter to continue..."
                read
                ;;
            2)
                run_unit_tests
                echo ""
                print_info "Press Enter to continue..."
                read
                ;;
            3)
                run_good_citizen_validation
                echo ""
                print_info "Press Enter to continue..."
                read
                ;;
            0)
                return 0
                ;;
            *)
                print_error "Invalid option: $choice"
                sleep 2
                ;;
        esac
    done
}

################################################################################
# Force Collection Run
################################################################################

force_collection_run() {
    print_header "Force Collection Run"

    # Check if database exists
    if [ ! -f "$DB_FILE" ]; then
        print_error "Database not found"
        print_info "Run option 9 (First-Time Setup) first"
        return 1
    fi

    # Check if surface worker is running
    if ! is_service_running "surface_worker"; then
        print_error "Surface worker is NOT running!"
        print_info "Start it first with option 2 (Start All Services)"
        echo ""
        return 1
    fi

    print_info "This will trigger immediate collection for all enabled surfaces"
    print_info "and monitor progress in real-time."
    echo ""

    cd "$SCRIPT_DIR"

    # Get list of enabled surfaces and trigger them
    print_step "Step 1/3: Querying enabled surfaces..."

    local surface_output=$(python3 manage.py shell -c "
from crawler_admin.models import TrendSurface
surfaces = TrendSurface.objects.filter(enabled=True).select_related('region')
count = surfaces.count()

if count == 0:
    print('NONE')
else:
    for s in surfaces:
        print(f'{s.region.key}/{s.key}')
" 2>&1 | grep -v "virtualenvwrapper" | grep -v "hook_loader" | grep -v "ModuleNotFoundError")

    if [ "$surface_output" = "NONE" ]; then
        print_warning "No enabled surfaces found"
        echo ""
        return 0
    fi

    # Count surfaces
    local surface_count=$(echo "$surface_output" | wc -l)
    print_success "Found $surface_count enabled surface(s):"
    echo "$surface_output" | while read line; do
        echo "  - $line"
    done
    echo ""

    # Trigger collection by setting next_run_at to NULL
    print_step "Step 2/3: Triggering collection..."

    python3 manage.py shell -c "
from crawler_admin.models import TrendSurface
surfaces = TrendSurface.objects.filter(enabled=True)
for s in surfaces:
    s.next_run_at = None
    s.save()
" 2>&1 | grep -v "virtualenvwrapper" | grep -v "hook_loader" | grep -v "ModuleNotFoundError" > /dev/null

    print_success "Collection triggered - surfaces will be polled within 60 seconds"
    echo ""

    # Monitor progress
    print_step "Step 3/3: Monitoring progress (waiting for collections to complete)..."
    echo ""
    print_info "Waiting for worker to pick up surfaces..."

    # Wait a bit for worker to start
    sleep 3

    # Monitor CrawlRun records
    local timeout=180  # 3 minutes max wait
    local elapsed=0
    local check_interval=3
    local completed_count=0

    # Create a timestamp marker
    local start_time=$(date -u +"%Y-%m-%d %H:%M:%S")

    while [ $elapsed -lt $timeout ]; do
        # Check for new CrawlRun records
        local status_output=$(python3 manage.py shell -c "
from crawler_admin.models import CrawlRun, TrendSurface
from django.utils import timezone
from datetime import datetime

# Get surfaces we're monitoring
surfaces = list(TrendSurface.objects.filter(enabled=True).select_related('region'))
surface_ids = [s.id for s in surfaces]

# Get recent runs for these surfaces (since trigger time)
start_time = datetime.fromisoformat('$start_time'.replace(' ', 'T') + '+00:00')
recent_runs = CrawlRun.objects.filter(
    surface_id__in=surface_ids,
    created_at__gte=start_time
).select_related('surface', 'surface__region').order_by('created_at')

completed = []
for run in recent_runs:
    surface_name = f'{run.surface.region.key}/{run.surface.key}'
    status = '✅' if run.status == 'success' else '❌'
    completed.append(f'{status}|{surface_name}|{run.fetched_count}|{run.stored_new_count}|{run.deduped_count}|{run.duration_ms}|{run.status}')

if completed:
    for c in completed:
        print(c)
else:
    print('WAITING')
" 2>&1 | grep -v "virtualenvwrapper" | grep -v "hook_loader" | grep -v "ModuleNotFoundError")

        # Check if we got results
        if [ "$status_output" != "WAITING" ] && [ ! -z "$status_output" ]; then
            # Clear previous output and show results
            echo -e "\r\033[K${BOLD}Collection Results:${NC}"
            echo ""

            completed_count=$(echo "$status_output" | wc -l)

            echo "$status_output" | while IFS='|' read -r emoji surface_name fetched stored deduped duration status; do
                echo -e "  $emoji ${BOLD}$surface_name${NC}"
                echo "     Fetched: $fetched items | Stored new: $stored | Deduped: $deduped"
                echo "     Duration: ${duration}ms | Status: $status"
                echo ""
            done

            # Check if all surfaces completed
            if [ "$completed_count" -ge "$surface_count" ]; then
                print_success "All $surface_count surface(s) completed collection!"
                break
            fi
        else
            # Show waiting indicator
            local dots=$((elapsed / check_interval % 4))
            local dot_string=""
            for i in $(seq 1 $dots); do
                dot_string="${dot_string}."
            done
            echo -ne "\r  Waiting for collections to complete${dot_string}   "
        fi

        sleep $check_interval
        elapsed=$((elapsed + check_interval))
    done

    echo ""

    # Timeout check
    if [ $elapsed -ge $timeout ]; then
        echo ""
        print_warning "Monitoring timeout reached (${timeout}s)"
        print_info "Collections may still be running. Check logs:"
        print_info "  tail -f $LOG_DIR/surface_worker.log"
    fi

    echo ""
    print_info "Collection run complete. Check Django Admin for detailed results."
    echo ""
}


################################################################################
# Main Menu
################################################################################

show_pipeline_queue_status() {
    print_header "Pipeline Queue Status"

    python3 manage.py shell -c "
from crawler_admin.models import TrendItem, ItemDerivation
from django.db.models import Count, Exists, OuterRef

LOCALES = ['en','zh','ja','ko','ru','pt','de','es','fr','ar','hi','id','tr','it','pl']

# Summarization queue per locale
print('--- Summarization Queue ---')
print(f'  {\"Locale\":<8} {\"Queued\":>8} {\"Complete\":>10} {\"Pending\":>9} {\"Total\":>8}')
print('  ' + '-'*47)
total_q = total_c = total_p = total_t = 0
for lg in LOCALES:
    q = TrendItem.objects.filter(lang_group=lg, summary_status='queued').count()
    c = TrendItem.objects.filter(lang_group=lg, summary_status='complete').count()
    p = TrendItem.objects.filter(lang_group=lg, summary_status='pending').count()
    t = TrendItem.objects.filter(lang_group=lg).count()
    if t == 0:
        continue
    print(f'  {lg:<8} {q:>8} {c:>10} {p:>9} {t:>8}')
    total_q += q; total_c += c; total_p += p; total_t += t
print('  ' + '-'*47)
print(f'  {\"TOTAL\":<8} {total_q:>8} {total_c:>10} {total_p:>9} {total_t:>8}')

# Translation queue per locale
print()
print('--- Translation Queue (ready to translate to zh-Hans) ---')
has_translation = ItemDerivation.objects.filter(
    item_id=OuterRef('id'),
    derivation_type='translation',
    target_locale='zh-Hans',
    status='complete'
)
print(f'  {\"Locale\":<8} {\"Summarized\":>12} {\"Done\":>8}')
print('  ' + '-'*32)
total_r = total_d = 0
for lg in LOCALES:
    if lg == 'zh':
        continue
    ready = TrendItem.objects.filter(
        lang_group=lg,
        summary_status__in=['complete','skipped'],
        display_status_zh_hans='pending'
    ).exclude(Exists(has_translation)).count()
    done = ItemDerivation.objects.filter(
        item__lang_group=lg,
        derivation_type='translation',
        target_locale='zh-Hans',
        status='complete'
    ).count()
    if ready == 0 and done == 0:
        continue
    print(f'  {lg:<8} {ready:>12} {done:>8}')
    total_r += ready; total_d += done
print('  ' + '-'*32)
print(f'  {\"TOTAL\":<8} {total_r:>12} {total_d:>8}')
"
}

show_menu() {
    clear
    echo -e "${BOLD}${CYAN}"
    cat << "EOF"
╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║        Culture-Flexible Trend Crawler Manager            ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝
EOF
    echo -e "${NC}"

    echo -e "${BOLD}Services:${NC}"
    echo "  1)  Start / Restart All Services"
    echo "  2)  Stop All Services"
    echo "  3)  Show Service Status"
    echo "  4)  Force Collection Run (All Surfaces)"
    echo ""

    echo -e "${BOLD}Information & Monitoring:${NC}"
    echo "  5)  Show Access URLs"
    echo "  6)  View Logs (interactive)"
    echo "  7)  Pipeline Queue Status (summarize / translate)"
    echo ""

    echo -e "${BOLD}Maintenance:${NC}"
    echo "  8)  Check System Requirements"
    echo "  9)  Update Dependencies"
    echo "  10) Run Migrations"
    echo ""

    echo -e "${BOLD}Database:${NC}"
    echo "  11) Backup Database"
    echo "  12) Restore Database"
    echo "  13) Reset Database (destructive!)"
    echo "  14) Create DB User"
    echo ""

    echo -e "${BOLD}Testing:${NC}"
    echo "  15) Run Tests"
    echo ""

    echo "  0)  Exit"
    echo ""
    echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
}

main_loop() {
    while true; do
        show_menu
        read -p "Select option: " choice

        case $choice in
            1) start_or_restart_services ;;
            2) stop_all_services ;;
            3) show_service_status ;;
            4) force_collection_run ;;
            5) show_urls ;;
            6) view_logs ;;
            7) show_pipeline_queue_status ;;
            8) check_requirements ;;
            9) update_dependencies ;;
            10) run_migrations ;;
            11) backup_database ;;
            12) restore_database ;;
            13) reset_database ;;
            14) create_db_user ;;
            15) test_menu ;;
            0)
                echo ""
                print_info "Exiting..."
                echo ""
                exit 0
                ;;
            *)
                print_error "Invalid option: $choice"
                sleep 2
                ;;
        esac

        if [ "$choice" != "0" ]; then
            echo ""
            read -p "Press Enter to continue..." -r
        fi
    done
}

################################################################################
# Entry Point
################################################################################

# Check if running from correct directory
if [ ! -f "${SCRIPT_DIR}/manage.py" ]; then
    print_error "This script must be run from the project root directory"
    print_info "Expected to find manage.py in: $SCRIPT_DIR"
    exit 1
fi

# Handle command-line arguments for automation
if [ $# -gt 0 ]; then
    case "$1" in
        start|setup|restart)
            start_or_restart_services
            ;;
        stop)
            stop_all_services
            ;;
        status)
            show_service_status
            ;;
        urls)
            show_urls
            ;;
        backup)
            backup_database
            ;;
        migrate-db|migrate)
            run_migrations
            ;;
        queue|pipeline)
            show_pipeline_queue_status
            ;;
        *)
            echo "Usage: $0 [start|stop|restart|status|urls|backup|migrate|info]"
            echo "Or run without arguments for interactive menu"
            exit 1
            ;;
    esac
else
    # Interactive mode - disable exit on error for menu loop
    set +e
    main_loop
fi
