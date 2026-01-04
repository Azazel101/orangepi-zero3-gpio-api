#!/bin/bash

# Check if running as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (use sudo)"
  exit 1
fi

APP_DIR="/root/opi_gpio_app"
SERVICE_FILE="opi_gpio.service"

echo "--- Installing Orange Pi GPIO API ---"

# 1. Install system dependencies
echo "Installing system dependencies..."
apt update
apt install -y python3-venv libgpiod-dev gpiod git

# 2. Setup Virtual Environment
echo "Setting up Python virtual environment..."
if [ ! -d "$APP_DIR/venv" ]; then
    python3 -m venv "$APP_DIR/venv"
fi

# 3. Install Python requirements
echo "Installing Python requirements..."
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# 4. Install Systemd Service
echo "Installing systemd service..."
if [ -f "$APP_DIR/$SERVICE_FILE" ]; then
    cp "$APP_DIR/$SERVICE_FILE" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable "$SERVICE_FILE"
    systemctl restart "$SERVICE_FILE"
    echo "Service installed and started."
else
    echo "Error: $SERVICE_FILE not found in $APP_DIR"
fi

# 5. Initialize Git for OTA (if not already a git repo)
echo "Initializing Git for OTA updates..."
if [ ! -d "$APP_DIR/.git" ]; then
    cd "$APP_DIR"
    git init
    git remote add origin https://github.com/Azazel101/orangepi-zero3-gpio-api.git
    git fetch --all
    git reset --hard origin/main
    echo "Git initialized and linked to origin/main"
else
    # Ensure remote is clean
    cd "$APP_DIR"
    git config --global --add safe.directory "$APP_DIR"
    current_remote=$(git remote get-url origin 2>/dev/null)
    if [ "$current_remote" != "https://github.com/Azazel101/orangepi-zero3-gpio-api.git" ]; then
        echo "Updating git remote..."
        git remote remove origin
        git remote add origin https://github.com/Azazel101/orangepi-zero3-gpio-api.git
    fi
     git fetch --all
fi

echo "--- Installation Complete ---"
echo "You can check status with: systemctl status $SERVICE_FILE"
echo "API is running on: http://$(hostname -I | awk '{print $1}'):8000"
