#!/bin/bash
set -uo pipefail

# ============================================================================
# LoxIO Core - Safe OTA Update Script with Rollback
# ============================================================================

APP_DIR="/root/opi_gpio_app"
SERVICE="opi_gpio.service"
WEB_SERVICE="opi_web.service"
LOG_FILE="$APP_DIR/app.log"
HEALTH_URL="http://127.0.0.1:8000/health"
GIT_TIMEOUT=60

log() {
    local msg="$(date '+%Y-%m-%d %H:%M:%S'): $1"
    echo "$msg" >> "$LOG_FILE"
    echo "$msg"
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
        delay=$((delay * 2))
        if [ $delay -gt $max_delay ]; then
            delay=$max_delay
        fi
    done
    return 1
}

# Change to app directory
cd "$APP_DIR" || { log "ERROR: Failed to cd to $APP_DIR"; exit 1; }

# 1. Save current state
COMMIT_BEFORE=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
log "=== Starting LoxIO Core Safe Update ==="
log "Current commit: $COMMIT_BEFORE"

# 2. Fetch updates with timeout
log "Fetching changes from origin..."
if ! timeout $GIT_TIMEOUT git fetch origin main 2>&1; then
    log "ERROR: Git fetch failed or timed out after ${GIT_TIMEOUT}s"
    exit 1
fi

# 3. Check if update is needed
REMOTE_COMMIT=$(git rev-parse origin/main 2>/dev/null || echo "unknown")
if [ "$COMMIT_BEFORE" == "$REMOTE_COMMIT" ]; then
    log "No changes detected (already at $COMMIT_BEFORE)"
    log "Restarting services anyway..."
else
    log "New version available: $REMOTE_COMMIT"
fi

# 4. Apply update
log "Applying update..."
if ! git reset --hard origin/main 2>&1; then
    log "ERROR: Git reset failed"
    exit 1
fi

NEW_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
log "Updated to commit: $NEW_COMMIT"

# 5. Restart services
log "Restarting service $SERVICE..."
systemctl restart "$SERVICE" || log "Warning: Failed to restart $SERVICE"
systemctl restart "$WEB_SERVICE" 2>/dev/null || true

# 6. Verify health with exponential backoff
log "Verifying service health..."
if wait_for_healthy; then
    log "SUCCESS: Update verified, system is healthy"
    log "=== LoxIO Core Safe Update Completed ==="
    exit 0
fi

# 7. Rollback on failure
log "CRITICAL: Update FAILED - API not responding"
log "Rolling back to $COMMIT_BEFORE..."

if git reset --hard "$COMMIT_BEFORE" 2>&1; then
    log "Rollback applied, restarting services..."
    systemctl restart "$SERVICE" || true
    systemctl restart "$WEB_SERVICE" 2>/dev/null || true

    # Verify rollback
    sleep 5
    if wait_for_healthy; then
        log "Rollback successful - system restored to previous version"
    else
        log "CRITICAL: Rollback also failed - manual intervention required!"
    fi
else
    log "CRITICAL: Rollback failed - manual intervention required!"
fi

log "=== LoxIO Core Safe Update Failed ==="
exit 1
