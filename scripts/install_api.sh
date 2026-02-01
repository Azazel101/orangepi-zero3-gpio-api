#!/bin/bash
set -euo pipefail

# ============================================================================
# LoxIO Core - API Installation Script
# ============================================================================

APP_DIR="/root/opi_gpio_app"
SERVICE_FILE="opi_gpio.service"
WEB_SERVICE_FILE="opi_web.service"
GIT_REPO="https://github.com/Azazel101/orangepi-zero3-gpio-api.git"

log() {
    echo "[$(date '+%H:%M:%S')] $1"
}

error() {
    echo "[$(date '+%H:%M:%S')] ERROR: $1" >&2
    exit 1
}

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    error "Please run as root (use sudo)"
fi

log "=== Installing LoxIO Core API (by RS Soft) ==="

# 1. Install system dependencies
log "Installing system dependencies..."
apt update -o Acquire::http::Timeout=30 || error "apt update failed"
apt install -y python3-venv libgpiod-dev gpiod git network-manager avahi-daemon || error "apt install failed"

# 2. Setup Virtual Environment
log "Setting up Python virtual environment..."
if [ ! -d "$APP_DIR/venv" ]; then
    python3 -m venv "$APP_DIR/venv" || error "Failed to create virtual environment"
fi

# 3. Install Python requirements
log "Installing Python requirements..."
"$APP_DIR/venv/bin/pip" install --upgrade pip --quiet || error "pip upgrade failed"
if [ -f "$APP_DIR/requirements.txt" ]; then
    "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" --quiet || error "pip install failed"
else
    log "Warning: requirements.txt not found, skipping pip install"
fi

# 4. Install Systemd Services
log "Installing systemd services..."
if [ -f "$APP_DIR/$SERVICE_FILE" ]; then
    cp "$APP_DIR/$SERVICE_FILE" /etc/systemd/system/
    log "Installed $SERVICE_FILE"
else
    error "$SERVICE_FILE not found in $APP_DIR"
fi

if [ -f "$APP_DIR/$WEB_SERVICE_FILE" ]; then
    cp "$APP_DIR/$WEB_SERVICE_FILE" /etc/systemd/system/
    log "Installed $WEB_SERVICE_FILE"
fi

systemctl daemon-reload
systemctl enable "$SERVICE_FILE"
systemctl restart "$SERVICE_FILE" || log "Warning: Service restart failed (may be first install)"

# 5. Set permissions for scripts
log "Setting script permissions..."
chmod +x "$APP_DIR"/scripts/*.sh 2>/dev/null || true
chmod +x "$APP_DIR"/scripts/*.exp 2>/dev/null || true

# 6. Ensure NetworkManager is running
log "Configuring NetworkManager..."
systemctl enable NetworkManager || true
systemctl restart NetworkManager || log "Warning: NetworkManager restart failed"

# 7. Initialize Git for OTA
log "Configuring Git for OTA updates..."
cd "$APP_DIR" || error "Cannot change to $APP_DIR"

git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true

if [ ! -d "$APP_DIR/.git" ]; then
    git init || error "git init failed"
    git remote add origin "$GIT_REPO" || true
    timeout 60 git fetch --all || log "Warning: git fetch failed (offline?)"
    git reset --hard origin/main 2>/dev/null || log "Warning: git reset failed"
    log "Git initialized and linked to origin/main"
else
    current_remote=$(git remote get-url origin 2>/dev/null || echo "")
    if [ "$current_remote" != "$GIT_REPO" ]; then
        log "Updating git remote..."
        git remote remove origin 2>/dev/null || true
        git remote add origin "$GIT_REPO"
    fi
    timeout 60 git fetch --all 2>/dev/null || log "Warning: git fetch failed (offline?)"
fi

# 8. Set Unique Hostname based on ChipID
log "Checking for Sunxi ChipID..."
if [ -f "/sys/class/sunxi_info/sys_info" ]; then
    CHIP_ID=$(grep "sunxi_chipid" /sys/class/sunxi_info/sys_info 2>/dev/null | awk '{print $3}' || echo "")
    if [ -n "$CHIP_ID" ]; then
        UNIQUE_ID=$(echo -n "$CHIP_ID" | md5sum | cut -c1-10 | tr '[:lower:]' '[:upper:]')
        NEW_HOSTNAME="LoxIO-$UNIQUE_ID"
        hostnamectl set-hostname "$NEW_HOSTNAME" || log "Warning: hostnamectl failed"
        # Backup hosts file before modification
        cp /etc/hosts /etc/hosts.bak
        sed -i "s/127.0.1.1.*/127.0.1.1\t$NEW_HOSTNAME/g" /etc/hosts || cp /etc/hosts.bak /etc/hosts
        log "Hostname updated to $NEW_HOSTNAME"
    fi
else
    log "Not running on Sunxi hardware, skipping hostname setup"
fi

# 9. Setup mDNS (Avahi)
log "Configuring mDNS..."
mkdir -p /etc/avahi/services
cat <<EOF > /etc/avahi/services/opi-gpio.service
<?xml version="1.0" standalone='no'?><!--*-nxml-*-->
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">LoxIO Core %h</name>
  <service>
    <type>_http._tcp</type>
    <port>5000</port>
  </service>
</service-group>
EOF

systemctl enable avahi-daemon || true
systemctl restart avahi-daemon || log "Warning: avahi-daemon restart failed"

# Final status
log "=== Installation Complete ==="
log "Check status: systemctl status $SERVICE_FILE"
IP_ADDR=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
log "API running at: http://${IP_ADDR}:8000"
log "mDNS: http://$(hostname).local:8000"
