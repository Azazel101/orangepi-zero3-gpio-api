#!/bin/bash

APP_DIR="/root/opi_gpio_app"
SERVICE="opi_gpio.service"
LOG_FILE="$APP_DIR/app.log"
HEALTH_URL="http://127.0.0.1:8000/health"

log() {
    echo "$(date): $1" >> "$LOG_FILE"
}

cd "$APP_DIR" || { log "Failed to cd to $APP_DIR"; exit 1; }

# 1. Save current state
COMMIT_BEFORE=$(git rev-parse HEAD)
log "--- Starting LoxIO Core Safe Update ---"
log "Current commit: $COMMIT_BEFORE"

# 2. Update
log "Fetching changes from origin..."
# Fetch and reset is safer than pull for appliances to avoid merge conflicts
git fetch origin main
if [ $? -ne 0 ]; then
    log "Git fetch failed! Aborting update."
    exit 1
fi

git reset --hard origin/main
if [ $? -ne 0 ]; then
    log "Git reset failed! Aborting update."
    exit 1
fi

NEW_COMMIT=$(git rev-parse HEAD)
log "Updated to commit: $NEW_COMMIT"

if [ "$COMMIT_BEFORE" == "$NEW_COMMIT" ]; then
    log "No changes detected (hashes match). Restarting just in case."
fi

# 3. Restart Service
log "Restarting service $SERVICE..."
systemctl restart "$SERVICE"

# 4. Watchdog Verify
log "Waiting for service to become healthy..."
MAX_RETRIES=20 # 20 * 2s = 40 seconds
SUCCESS=0

for i in $(seq 1 $MAX_RETRIES); do
    sleep 2
    # Check if endpoint returns 200 OK and contains "healthy"
    if curl -s "$HEALTH_URL" | grep -q "healthy"; then
        SUCCESS=1
        break
    fi
    log "Check $i/$MAX_RETRIES: Service not ready yet..."
done

if [ $SUCCESS -eq 1 ]; then
    log "Update Verified! System is healthy."
else
    log "CRITICAL: Update FAILED! API not responding. Rolling back to $COMMIT_BEFORE..."
    
    # 5. Rollback
    git reset --hard "$COMMIT_BEFORE"
    systemctl restart "$SERVICE"
    
    log "Rollback completed. System restored to previous stable version."
fi
