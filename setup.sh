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

# Check if a service is running
is_service_running() {
    local service_name="$1"
    local pid_file="${PID_DIR}/${service_name}.pid"

    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        if ps -p "$pid" > /dev/null 2>&1; then
            return 0  # Running
        else
            # Stale PID file
            rm -f "$pid_file"
            return 1  # Not running
        fi
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

# Get PID for a service
get_service_pid() {
    local service_name="$1"
    local pid_file="${PID_DIR}/${service_name}.pid"

    if [ -f "$pid_file" ]; then
        cat "$pid_file"
    else
        echo ""
    fi
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

        # Check for required environment variables
        local required_vars=("DJANGO_SECRET_KEY" "DEEPL_API_KEY" "OPENAI_API_KEY")
        for var in "${required_vars[@]}"; do
            if grep -q "^${var}=" "$ENV_FILE" && ! grep -q "^${var}=your-" "$ENV_FILE"; then
                print_success "  $var is configured"
            else
                print_warning "  $var needs configuration"
            fi
        done
    else
        print_error ".env file not found"
        print_info "Run option 8 (First-Time Setup) to create it from .env.example"
        all_ok=false
    fi

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
# First-Time Setup
################################################################################

first_time_setup() {
    print_header "First-Time Setup"

    ensure_directories

    # Step 1: Create .env file
    print_step "Step 1/6: Setting up environment file..."
    if [ ! -f "$ENV_FILE" ]; then
        if [ -f "${SCRIPT_DIR}/.env.example" ]; then
            cp "${SCRIPT_DIR}/.env.example" "$ENV_FILE"
            print_success ".env file created from .env.example"
            print_warning "Please edit .env and add your API keys:"
            print_info "  - DJANGO_SECRET_KEY (generate a random string)"
            print_info "  - DEEPL_API_KEY (from https://www.deepl.com/pro-api)"
            print_info "  - OPENAI_API_KEY (from https://platform.openai.com/api-keys)"
            echo ""
            read -p "Press Enter after updating .env file..." -r
        else
            print_error ".env.example not found"
            return 1
        fi
    else
        print_info ".env file already exists (skipping)"
    fi

    # Step 2: Install dependencies
    print_step "Step 2/6: Installing Python dependencies..."
    if [ -f "${SCRIPT_DIR}/requirements.txt" ]; then
        pip install -r "${SCRIPT_DIR}/requirements.txt"
        print_success "Dependencies installed"
    else
        print_error "requirements.txt not found"
        return 1
    fi

    # Step 3: Run migrations
    print_step "Step 3/6: Running database migrations..."
    cd "$SCRIPT_DIR"
    python3 manage.py migrate
    print_success "Database migrations complete"

    # Step 4: Load initial data
    print_step "Step 4/6: Loading initial data (regions & surfaces)..."
    if [ -f "${SCRIPT_DIR}/src/crawler_admin/fixtures/initial_data.json" ]; then
        python3 manage.py loaddata initial_data
        print_success "Initial data loaded"
    else
        print_warning "initial_data.json not found (skipping)"
    fi

    # Step 5: Create superuser
    print_step "Step 5/6: Creating Django admin superuser..."
    print_info "You'll be prompted to create an admin account"
    python3 manage.py createsuperuser
    print_success "Superuser created"

    # Step 6: Verify setup
    print_step "Step 6/6: Verifying setup..."
    if [ -f "$DB_FILE" ]; then
        print_success "Database created successfully"
    else
        print_error "Database file not found"
        return 1
    fi

    echo ""
    print_success "Setup complete! You can now start the services."
    print_info "Use option 2 to start all services, or option 1 for quick start"
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

    # Start service in background with nohup
    cd "$SCRIPT_DIR"
    nohup bash -c "$start_command" > "$log_file" 2>&1 &
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
        return 0
    fi

    local pid=$(get_service_pid "$service_name")
    print_step "Stopping $service_description (PID: $pid)..."

    # Try graceful shutdown first
    kill $pid 2>/dev/null || true

    # Wait up to 10 seconds for graceful shutdown
    for i in {1..10}; do
        if ! ps -p $pid > /dev/null 2>&1; then
            rm -f "$pid_file"
            print_success "$service_description stopped"
            return 0
        fi
        sleep 1
    done

    # Force kill if still running
    print_warning "Forcing shutdown..."
    kill -9 $pid 2>/dev/null || true
    rm -f "$pid_file"
    print_success "$service_description stopped (forced)"
}

start_all_services() {
    print_header "Starting All Services"

    ensure_directories

    # Check .env file
    if [ ! -f "$ENV_FILE" ]; then
        print_error ".env file not found"
        print_info "Run option 8 (First-Time Setup) first"
        return 1
    fi

    # Check database
    if [ ! -f "$DB_FILE" ]; then
        print_error "Database not found"
        print_info "Run option 8 (First-Time Setup) first"
        return 1
    fi

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

    start_service "translation_worker" "Translation Worker" \
        "${SCRIPT_DIR}/scripts/run_translation_worker.sh"

    echo ""
    print_success "All services started!"
    print_info "Use option 5 to check service status"
    print_info "Use option 6 to view access URLs"
    echo ""
}

stop_all_services() {
    print_header "Stopping All Services"

    stop_service "translation_worker" "Translation Worker"
    stop_service "surface_worker" "Surface Worker"
    stop_service "api_server" "FastAPI Server"
    stop_service "django_admin" "Django Admin Server"

    echo ""
    print_success "All services stopped"
    echo ""
}

restart_all_services() {
    print_header "Restarting All Services"
    stop_all_services
    sleep 2
    start_all_services
}

show_service_status() {
    print_header "Service Status"

    local services=(
        "django_admin:Django Admin Server:$DJANGO_PORT"
        "api_server:FastAPI Server:$API_PORT"
        "surface_worker:Surface Worker:-"
        "translation_worker:Translation Worker:-"
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
    print_header "Access URLs"

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

    echo -e "${BOLD}Django Admin Interface:${NC}"
    echo -e "  ${CYAN}http://$HOST_IP:$DJANGO_PORT/admin${NC}"
    echo -e "  Configure regions, surfaces, and view collected data"
    echo ""

    echo -e "${BOLD}FastAPI Server:${NC}"
    echo -e "  ${CYAN}http://$HOST_IP:$API_PORT${NC}"
    echo -e "  Main API endpoint"
    echo ""

    echo -e "${BOLD}API Documentation:${NC}"
    echo -e "  ${CYAN}http://$HOST_IP:$API_PORT/docs${NC} (Swagger UI)"
    echo -e "  ${CYAN}http://$HOST_IP:$API_PORT/redoc${NC} (ReDoc)"
    echo ""

    echo -e "${BOLD}Health & Monitoring:${NC}"
    echo -e "  ${CYAN}http://$HOST_IP:$API_PORT/health${NC}"
    echo -e "  ${CYAN}http://$HOST_IP:$API_PORT/api/v1/health/crawl${NC}"
    echo -e "  ${CYAN}http://$HOST_IP:$API_PORT/api/v1/health/translation${NC}"
    echo ""

    echo -e "${BOLD}Data Endpoints:${NC}"
    echo -e "  ${CYAN}http://$HOST_IP:$API_PORT/api/v1/regions${NC}"
    echo -e "  ${CYAN}http://$HOST_IP:$API_PORT/api/v1/surfaces${NC}"
    echo -e "  ${CYAN}http://$HOST_IP:$API_PORT/api/v1/trends${NC}"
    echo ""

    # Check if services are actually running
    local services_running=false
    if is_service_running "django_admin" || is_service_running "api_server"; then
        services_running=true
        print_success "Services are running - URLs should be accessible"
    else
        print_warning "Services are not running - start them with option 2"
    fi

    echo ""
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
    echo -e "${BOLD}${RED}Type 'yes' to confirm and press Enter (or anything else to cancel):${NC}"
    read -p "> " confirmation

    if [ "$confirmation" != "yes" ]; then
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

    # Load initial data
    print_step "Loading initial data..."
    if [ -f "${SCRIPT_DIR}/src/crawler_admin/fixtures/initial_data.json" ]; then
        python3 manage.py loaddata initial_data
        print_success "Initial data loaded"
    fi

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

run_tests() {
    print_header "Run Tests"

    local test_script="${SCRIPT_DIR}/scripts/test_workflow.sh"

    if [ ! -f "$test_script" ]; then
        print_error "Test script not found: $test_script"
        return 1
    fi

    print_info "Running test workflow in DRY_RUN mode..."
    print_info "This will test surface collectors without storing data"
    echo ""

    bash "$test_script"

    echo ""
    print_success "Test complete"
    echo ""
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
# Quick Start
################################################################################

quick_start() {
    print_header "Quick Start - Complete Setup & Launch"

    print_info "This will perform complete setup and start all services"
    echo ""
    confirm_action "Continue with quick start?" || return 1

    # Check if already set up
    if [ -f "$ENV_FILE" ] && [ -f "$DB_FILE" ]; then
        print_info "System appears to be already set up"
        read -p "Skip setup and just start services? (Y/n): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
            start_all_services
            show_urls
            return 0
        fi
    fi

    # Run first-time setup
    first_time_setup

    if [ $? -eq 0 ]; then
        echo ""
        print_step "Setup complete! Starting services..."
        sleep 2
        start_all_services
        show_urls
    else
        print_error "Setup failed. Please check errors above."
    fi
}

################################################################################
# Setup Migrated Collectors
################################################################################

setup_migrated_collectors() {
    print_header "Setup Migrated Collectors"

    # Check if database exists
    if [ ! -f "$DB_FILE" ]; then
        print_error "Database not found"
        print_info "Run option 9 (First-Time Setup) first"
        return 1
    fi

    echo ""
    print_info "This will automatically configure 14 new collector sources:"
    echo ""
    echo "  News Sources (10):"
    echo "    • BBC, Google News, Reuters, AP, Guardian, Al Jazeera"
    echo "    • Billboard, Variety, IGN, Polygon"
    echo ""
    echo "  Social Media (4):"
    echo "    • Hacker News, Google Trends"
    echo "    • YouTube (enhanced), Twitter (placeholder)"
    echo ""
    print_warning "This is idempotent - safe to run multiple times"
    echo ""

    confirm_action "Continue with collector setup?" || return 1

    echo ""
    print_step "Running setup command..."
    echo ""

    cd "$SCRIPT_DIR"

    # Run the Django management command
    python3 manage.py setup_migrated_collectors

    local exit_code=$?

    if [ $exit_code -eq 0 ]; then
        echo ""
        print_success "Collectors configured successfully!"
        echo ""

        # Ask about restarting services
        print_info "Services should be restarted to load new collectors"
        read -p "Restart services now? (Y/n): " -n 1 -r
        echo

        if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
            restart_all_services
        else
            print_info "Remember to restart services later: ./setup.sh restart"
        fi
    else
        print_error "Setup failed. Check errors above."
        return 1
    fi

    echo ""
}

################################################################################
# Main Menu
################################################################################

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

    echo -e "${BOLD}Quick Actions:${NC}"
    echo "  1)  Quick Start (setup + start all)"
    echo "  2)  Start All Services"
    echo "  3)  Stop All Services"
    echo "  4)  Restart All Services"
    echo "  5)  Show Service Status"
    echo "  6)  Force Collection Run (All Surfaces)"
    echo ""

    echo -e "${BOLD}Information & Monitoring:${NC}"
    echo "  7)  Show Access URLs"
    echo "  8)  View Logs (interactive)"
    echo ""

    echo -e "${BOLD}Setup & Maintenance:${NC}"
    echo "  9)  First-Time Setup"
    echo "  10) Check System Requirements"
    echo "  11) Update Dependencies"
    echo ""

    echo -e "${BOLD}Database Operations:${NC}"
    echo "  12) Backup Database"
    echo "  13) Restore Database"
    echo "  14) Reset Database (destructive!)"
    echo ""

    echo -e "${BOLD}Testing:${NC}"
    echo "  15) Run Tests"
    echo ""

    echo -e "${BOLD}Migration:${NC}"
    echo "  16) Setup Migrated Collectors (14 new sources)"
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
            1) quick_start ;;
            2) start_all_services ;;
            3) stop_all_services ;;
            4) restart_all_services ;;
            5) show_service_status ;;
            6) force_collection_run ;;
            7) show_urls ;;
            8) view_logs ;;
            9) first_time_setup ;;
            10) check_requirements ;;
            11) update_dependencies ;;
            12) backup_database ;;
            13) restore_database ;;
            14) reset_database ;;
            15) run_tests ;;
            16) setup_migrated_collectors ;;
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
        start)
            start_all_services
            ;;
        stop)
            stop_all_services
            ;;
        restart)
            restart_all_services
            ;;
        status)
            show_service_status
            ;;
        urls)
            show_urls
            ;;
        setup)
            first_time_setup
            ;;
        backup)
            backup_database
            ;;
        migrate)
            setup_migrated_collectors
            ;;
        *)
            echo "Usage: $0 [start|stop|restart|status|urls|setup|backup|migrate]"
            echo "Or run without arguments for interactive menu"
            exit 1
            ;;
    esac
else
    # Interactive mode
    main_loop
fi
