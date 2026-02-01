#!/bin/bash
set -euo pipefail

# ============================================================================
# LoxIO Core - Manual ZIP Update Script
# ============================================================================

APP_DIR="/root/opi_gpio_app"
LOG_FILE="$APP_DIR/app.log"
SERVICE="opi_gpio.service"
HEALTH_URL="http://127.0.0.1:8000/health"

# Create secure temp directory (not world-readable)
EXTRACT_DIR=$(mktemp -d -t loxio_update.XXXXXX)
chmod 700 "$EXTRACT_DIR"

# Cleanup function
cleanup() {
    rm -rf "$EXTRACT_DIR" 2>/dev/null || true
}
trap cleanup EXIT

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S'): $1" >> "$LOG_FILE"
    echo "$1"
}

# Exponential backoff health check
wait_for_healthy() {
    local max_attempts=10
    local delay=2
    local max_delay=32

    for ((i=1; i<=max_attempts; i++)); do
        if curl -sf -m 5 "$HEALTH_URL" | grep -q "healthy"; then
            return 0
        fi
        log "Health check $i/$max_attempts: Service not ready, waiting ${delay}s..."
        sleep $delay
        # Exponential backoff with max
        delay=$((delay * 2))
        if [ $delay -gt $max_delay ]; then
            delay=$max_delay
        fi
    done
    return 1
}

# Validate arguments
ZIP_FILE="${1:-}"
if [ -z "$ZIP_FILE" ]; then
    log "Error: ZIP file path not provided"
    echo "Usage: $0 <path_to_zip_file>"
    exit 1
fi

if [ ! -f "$ZIP_FILE" ]; then
    log "Error: ZIP file not found: $ZIP_FILE"
    exit 1
fi

# Validate ZIP file (basic integrity check)
if ! unzip -tq "$ZIP_FILE" > /dev/null 2>&1; then
    log "Error: ZIP file is corrupted or invalid"
    exit 1
fi

log "--- Starting LoxIO Core Manual ZIP Update ---"
log "Source: $ZIP_FILE"
log "Temp dir: $EXTRACT_DIR"

# Extract ZIP
log "Extracting ZIP file..."
if ! unzip -q "$ZIP_FILE" -d "$EXTRACT_DIR"; then
    log "Error: ZIP extraction failed"
    exit 1
fi

# Find the app root (GitHub zips have a subdir)
SUBDIR=$(find "$EXTRACT_DIR" -maxdepth 2 -name "main.py" -exec dirname {} \; | head -n1)

if [ -z "$SUBDIR" ]; then
    log "Error: Could not find application root in ZIP (missing main.py)"
    exit 1
fi

log "Found application root at: $SUBDIR"

# Backup current config with secure permissions
CONFIG_BACKUP=""
if [ -f "$APP_DIR/gpio_config.json" ]; then
    CONFIG_BACKUP=$(mktemp -t gpio_config.XXXXXX)
    chmod 600 "$CONFIG_BACKUP"
    cp "$APP_DIR/gpio_config.json" "$CONFIG_BACKUP"
    log "Preserved existing GPIO config"
fi

# Update application files
log "Updating application files..."
cp -a "$SUBDIR"/* "$APP_DIR/"

# Restore config
if [ -n "$CONFIG_BACKUP" ] && [ -f "$CONFIG_BACKUP" ]; then
    cp "$CONFIG_BACKUP" "$APP_DIR/gpio_config.json"
    rm -f "$CONFIG_BACKUP"
    log "Restored GPIO config"
fi

# Restart services
log "Restarting services..."
systemctl restart "$SERVICE" || log "Warning: Failed to restart $SERVICE"
systemctl restart opi_web.service || log "Warning: Failed to restart opi_web.service"

# Verify health with exponential backoff
log "Verifying service health..."
if wait_for_healthy; then
    log "SUCCESS: Manual update completed, system is healthy"

    # Cleanup source ZIP on success
    rm -f "$ZIP_FILE" 2>/dev/null || true
else
    log "CRITICAL: Manual update FAILED - API not responding"
    log "Manual intervention may be required"
    exit 1
fi

log "--- LoxIO Core Manual Update Finished ---"
