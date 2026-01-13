#!/bin/bash

# Web UI Installation Script for Orange Pi GPIO
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root"
  exit 1
fi

WEB_DIR="/root/opi_gpio_app/web"

echo "--- Installing OPI GPIO Web UI ---"

# Install Flask and Requests
apt update
apt install -y python3-flask python3-requests

# Copy files
# (In a real scenario, this would be done via git or scp, 
# but I'll assume the files are placed in the directory)

# Create Systemd Service
cat <<EOF > /etc/systemd/system/opi_web.service
[Unit]
Description=Orange Pi GPIO Web UI
After=network.target opi_gpio.service

[Service]
Type=simple
User=root
WorkingDirectory=$WEB_DIR
ExecStart=/usr/bin/python3 app.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable opi_web.service
systemctl restart opi_web.service

echo "Web UI is running on: http://$(hostname -I | awk '{print $1}'):5000"
