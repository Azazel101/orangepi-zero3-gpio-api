#!/bin/bash
set -uo pipefail

# ============================================================================
# LoxIO Core - Smart Restart Script
# Handles both systemd and manual process modes
# ============================================================================

APP_DIR="/root/opi_gpio_app"
API_SERVICE="opi_gpio.service"
WEB_SERVICE="opi_web.service"
SHUTDOWN_TIMEOUT=10

log() {
    echo "[$(date '+%H:%M:%S')] $1"
}

# Graceful shutdown function
graceful_shutdown() {
    local process_pattern=$1
    local pids

    pids=$(pgrep -f "$process_pattern" 2>/dev/null || echo "")
    if [ -z "$pids" ]; then
        return 0
    fi

    log "Sending SIGTERM to $process_pattern..."
    pkill -TERM -f "$process_pattern" 2>/dev/null || true

    # Wait for graceful shutdown
    local waited=0
    while [ $waited -lt $SHUTDOWN_TIMEOUT ]; do
        if ! pgrep -f "$process_pattern" > /dev/null 2>&1; then
            log "Process terminated gracefully"
            return 0
        fi
        sleep 1
        ((waited++))
    done

    # Force kill if still running
    log "Timeout reached, sending SIGKILL..."
    pkill -KILL -f "$process_pattern" 2>/dev/null || true
    sleep 1
}

# Check if systemd services are being used
if systemctl is-active --quiet "$API_SERVICE" 2>/dev/null; then
    log "Restarting via systemd..."

    systemctl restart "$API_SERVICE"
    systemctl restart "$WEB_SERVICE" 2>/dev/null || true

    # Brief wait and status check
    sleep 2
    if systemctl is-active --quiet "$API_SERVICE"; then
        log "API service restarted successfully"
    else
        log "Warning: API service may not have started correctly"
    fi
else
    log "Systemd service not active, restarting manually..."

    # Graceful shutdown of existing processes
    graceful_shutdown "main.py"
    graceful_shutdown "web/app.py"

    # Start API
    cd "$APP_DIR" || { log "ERROR: Cannot change to $APP_DIR"; exit 1; }

    if [ -x "$APP_DIR/venv/bin/python3" ]; then
        log "Starting API with venv..."
        nohup "$APP_DIR/venv/bin/python3" main.py >> "$APP_DIR/app.log" 2>&1 &
    else
        log "Starting API with system python..."
        nohup python3 main.py >> "$APP_DIR/app.log" 2>&1 &
    fi

    # Start Web UI
    if [ -f "$APP_DIR/web/app.py" ]; then
        cd "$APP_DIR/web" || true
        log "Starting Web UI..."
        nohup python3 app.py >> "$APP_DIR/app.log" 2>&1 &
    fi

    sleep 2

    # Verify processes started
    if pgrep -f "main.py" > /dev/null 2>&1; then
        log "API process started"
    else
        log "Warning: API process may not have started"
    fi
fi

log "Restart complete"
