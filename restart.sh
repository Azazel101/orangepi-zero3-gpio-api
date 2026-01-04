#!/bin/bash
# Smart restart script that handles both manual and systemd runs

if systemctl is-active --quiet opi_gpio.service; then
    echo "Restarting via systemd..."
    systemctl restart opi_gpio.service
else
    echo "Restarting manually..."
    pkill -9 -f main.py || true
    rm -f /root/opi_gpio_app/app.log
    cd /root/opi_gpio_app
    nohup /root/opi_gpio_app/venv/bin/python3 main.py > /root/opi_gpio_app/app.log 2>&1 &
fi
echo "App restarted"
