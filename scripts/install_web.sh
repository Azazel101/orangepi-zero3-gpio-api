#!/bin/bash
set -euo pipefail

# ============================================================================
# LoxIO Core - Web UI Installation Script
# ============================================================================

APP_DIR="/root/opi_gpio_app"
WEB_DIR="$APP_DIR/web"
SERVICE_FILE="opi_web.service"

log() {
    echo "[$(date '+%H:%M:%S')] $1"
}

error() {
    echo "[$(date '+%H:%M:%S')] ERROR: $1" >&2
    exit 1
}

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    error "Please run as root"
fi

log "=== Installing LoxIO Core Web UI (by RS Soft) ==="

# Verify web directory exists
if [ ! -d "$WEB_DIR" ]; then
    error "Web directory not found: $WEB_DIR"
fi

# Install Flask and Requests
log "Installing Python dependencies..."
apt update -o Acquire::http::Timeout=30 || error "apt update failed"
apt install -y python3-flask python3-requests || error "apt install failed"

# Install Systemd Service from file or create it
log "Installing systemd service..."
if [ -f "$APP_DIR/$SERVICE_FILE" ]; then
    cp "$APP_DIR/$SERVICE_FILE" /etc/systemd/system/
    log "Installed $SERVICE_FILE from repository"
else
    # Fallback: create service file
    cat <<EOF > /etc/systemd/system/opi_web.service
[Unit]
Description=LoxIO Core Web UI
Documentation=https://github.com/Azazel101/orangepi-zero3-gpio-api
After=network.target opi_gpio.service
Wants=opi_gpio.service

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=$WEB_DIR
ExecStart=/usr/bin/python3 app.py
Restart=on-failure
RestartSec=5
RestartMaxDelaySec=60
TimeoutStartSec=20
TimeoutStopSec=10
PrivateTmp=yes
NoNewPrivileges=yes
StandardOutput=journal
StandardError=journal
SyslogIdentifier=loxio-web

[Install]
WantedBy=multi-user.target
EOF
    log "Created $SERVICE_FILE"
fi

systemctl daemon-reload
systemctl enable opi_web.service
systemctl restart opi_web.service || log "Warning: Service restart failed"

# Verify service started
sleep 2
if systemctl is-active --quiet opi_web.service; then
    log "Web UI service started successfully"
else
    log "Warning: Web UI service may not have started correctly"
    systemctl status opi_web.service --no-pager || true
fi

log "=== Web UI Installation Complete ==="
IP_ADDR=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
log "Web UI running at: http://${IP_ADDR}:5000"
log "mDNS: http://$(hostname).local:5000"
