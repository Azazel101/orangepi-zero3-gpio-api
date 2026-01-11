#!/bin/bash

ZIP_FILE=$1
APP_DIR="/root/opi_gpio_app"
EXTRACT_DIR="/tmp/opi_update_extract"
LOG_FILE="$APP_DIR/app.log"
SERVICE="opi_gpio.service"
HEALTH_URL="http://127.0.0.1:8000/health"

log() {
    echo "$(date): $1" >> "$LOG_FILE"
}

if [ -z "$ZIP_FILE" ] || [ ! -f "$ZIP_FILE" ]; then
    log "Manual Update: ZIP file not found or not provided."
    exit 1
fi

log "--- Starting Manual ZIP Update ---"
log "Source: $ZIP_FILE"

# 1. Clean and Prepare Extract Dir
rm -rf "$EXTRACT_DIR"
mkdir -p "$EXTRACT_DIR"

# 2. Extract
unzip -q "$ZIP_FILE" -d "$EXTRACT_DIR"
if [ $? -ne 0 ]; then
    log "Manual Update: ZIP Extraction failed!"
    exit 1
fi

# 3. Find the app root (GitHub zips have a subdir)
# We look for main.py inside the extract dir
SUBDIR=$(find "$EXTRACT_DIR" -maxdepth 2 -name "main.py" -exec dirname {} \;)

if [ -z "$SUBDIR" ]; then
    log "Manual Update: Could not find application root in ZIP (missing main.py)."
    exit 1
fi

log "Found application root at: $SUBDIR"

# 4. Backup current config if it exists
# We want to preserve gpio_config.json
if [ -f "$APP_DIR/gpio_config.json" ]; then
    cp "$APP_DIR/gpio_config.json" "/tmp/gpio_config.json.bak"
    log "Preserving existing GPIO config."
fi

# 5. Overwrite APP_DIR contents
log "Updating application files..."
# Delete everything except .git and logs and venv to keep it clean but functional
# Actually, better to just copy over.
cp -r "$SUBDIR"/* "$APP_DIR/"

# Restore config
if [ -f "/tmp/gpio_config.json.bak" ]; then
    cp "/tmp/gpio_config.json.bak" "$APP_DIR/gpio_config.json"
fi

# 6. Restart Service
log "Restarting service $SERVICE..."
systemctl restart "$SERVICE"
systemctl restart opi_web.service

# 7. Watchdog Verify
log "Waiting for service to become healthy..."
MAX_RETRIES=20
SUCCESS=0

for i in $(seq 1 $MAX_RETRIES); do
    sleep 2
    if curl -s "$HEALTH_URL" | grep -q "healthy"; then
        SUCCESS=1
        break
    fi
    log "Check $i/$MAX_RETRIES: Service not ready yet..."
done

if [ $SUCCESS -eq 1 ]; then
    log "Manual Update Verified! System is healthy."
else
    log "CRITICAL: Manual Update FAILED! API not responding."
    # Since we don't have a git hash here easily to rollback, 
    # we just log failure. Manual intervention might be needed.
    exit 1
fi

# Cleanup
rm -rf "$EXTRACT_DIR"
rm -f "$ZIP_FILE"
log "--- Manual Update Finished ---"
