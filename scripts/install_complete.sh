#!/bin/bash

# Check if IP is provided
if [ -z "$1" ]; then
    echo "Usage: ./install_complete.sh <TARGET_IP>"
    echo "Example: ./install_complete.sh 192.168.1.100"
    exit 1
fi

TARGET_IP=$1
PASSWORD="paradox"

echo "--- Starting LoxIO Core Remote Installation on $TARGET_IP ---"
echo "WARNING: This will wipe specific existing data on the target for a fresh install."
read -p "Press Enter to continue..."

# Use expect to handle the SSH interaction
/usr/bin/expect <<EOF
set timeout 300
set ip "$TARGET_IP"
set password "$PASSWORD"

spawn ssh -o StrictHostKeyChecking=no root@\$ip
expect {
    "password:" { send "\$password\r" }
    "yes/no" { send "yes\r"; exp_continue }
}

# 1. Update and Install Git
expect "#"
send "echo '--- Step 1: Updating System ---'\r"
send "apt update && apt install -y git expect\r"

# 2. Clone Repository
expect "#"
send "echo '--- Step 2: Cloning Repository ---'\r"
send "rm -rf /root/opi_gpio_app\r"
send "git clone https://github.com/Azazel101/orangepi-zero3-gpio-api.git /root/opi_gpio_app\r"

# 3. Enter Directory
expect "#"
send "cd /root/opi_gpio_app\r"

# 4. Make scripts executable
expect "#"
send "chmod +x scripts/*.sh scripts/*.exp\r"

# 5. Run API Installer
expect "#"
send "echo '--- Step 3: Installing Hardware API ---'\r"
send "./scripts/install_api.sh\r"

# Wait significantly longer for install
set timeout 600

# 6. Run Web Installer
expect "#"
send "echo '--- Step 4: Installing Web UI ---'\r"
send "./scripts/install_web.sh\r"

# 7. Set Unique Hostname
expect "#"
send "echo '--- Step 5: Setting Unique Hostname ---'\r"
send "./scripts/set_hostname.sh\r"

# 8. Final Check
expect "#"
send "systemctl status opi_gpio.service --no-pager\r"
expect "#"
send "systemctl status opi_web.service --no-pager\r"

expect "#"
send "exit\r"
expect eof
EOF

echo "--- Remote Installation Session Finished ---"
echo "If successful, access the dashboard at http://$TARGET_IP:5000"
