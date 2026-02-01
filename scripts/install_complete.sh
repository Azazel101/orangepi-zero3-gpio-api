#!/bin/bash
set -euo pipefail

# ============================================================================
# LoxIO Core - Complete Remote Installation Script
# ============================================================================

# Validate IP address format
validate_ip() {
    local ip=$1
    if [[ $ip =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
        return 0
    fi
    return 1
}

# Check if IP is provided
if [ -z "${1:-}" ]; then
    echo "Usage: ./install_complete.sh <TARGET_IP> [PASSWORD]"
    echo "Example: ./install_complete.sh 192.168.1.100"
    echo ""
    echo "If PASSWORD is not provided, you will be prompted for it."
    exit 1
fi

TARGET_IP=$1

# Validate IP address
if ! validate_ip "$TARGET_IP"; then
    echo "Error: Invalid IP address format: $TARGET_IP"
    exit 1
fi

# Get password - either from argument or prompt (more secure)
if [ -n "${2:-}" ]; then
    PASSWORD="$2"
else
    echo -n "Enter SSH password for root@$TARGET_IP: "
    read -rs PASSWORD
    echo ""
fi

if [ -z "$PASSWORD" ]; then
    echo "Error: Password cannot be empty"
    exit 1
fi

echo "--- Starting LoxIO Core Remote Installation on $TARGET_IP ---"
echo "WARNING: This will wipe specific existing data on the target for a fresh install."
read -rp "Press Enter to continue or Ctrl+C to abort..."

# Check if expect is installed
if ! command -v expect &> /dev/null; then
    echo "Error: 'expect' is not installed. Install it with: apt install expect"
    exit 1
fi

# Use expect to handle the SSH interaction
/usr/bin/expect <<EXPECT_EOF
set timeout 300
set ip "$TARGET_IP"
set password "$PASSWORD"

# Error handling
proc abort {msg} {
    puts stderr "ERROR: \$msg"
    exit 1
}

spawn ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 root@\$ip
expect {
    timeout { abort "SSH connection timed out" }
    "Connection refused" { abort "SSH connection refused" }
    "No route to host" { abort "No route to host" }
    "password:" { send "\$password\r" }
    "yes/no" { send "yes\r"; exp_continue }
}

expect {
    timeout { abort "Authentication timed out" }
    "Permission denied" { abort "Authentication failed - wrong password" }
    "#" { }
}

# 1. Update and Install Git
send "echo '--- Step 1: Updating System ---'\r"
expect "#"
send "apt update -o Acquire::http::Timeout=30 && apt install -y git expect || echo 'APT_FAILED'\r"
expect {
    "APT_FAILED" { abort "Failed to install dependencies" }
    "#" { }
}

# 2. Clone Repository
send "echo '--- Step 2: Cloning Repository ---'\r"
expect "#"
send "rm -rf /root/opi_gpio_app\r"
expect "#"
send "timeout 120 git clone https://github.com/Azazel101/orangepi-zero3-gpio-api.git /root/opi_gpio_app || echo 'GIT_FAILED'\r"
expect {
    "GIT_FAILED" { abort "Failed to clone repository" }
    "#" { }
}

# 3. Enter Directory and setup
send "cd /root/opi_gpio_app\r"
expect "#"
send "chmod +x scripts/*.sh 2>/dev/null; chmod +x scripts/*.exp 2>/dev/null; true\r"
expect "#"

# 4. Run API Installer
send "echo '--- Step 3: Installing Hardware API ---'\r"
expect "#"
set timeout 600
send "./scripts/install_api.sh\r"
expect {
    timeout { abort "API installation timed out" }
    "#" { }
}

# 5. Run Web Installer
send "echo '--- Step 4: Installing Web UI ---'\r"
expect "#"
send "./scripts/install_web.sh\r"
expect {
    timeout { abort "Web UI installation timed out" }
    "#" { }
}

# 6. Set Unique Hostname
send "echo '--- Step 5: Setting Unique Hostname ---'\r"
expect "#"
send "./scripts/set_hostname.sh || true\r"
expect "#"

# 7. Final Check
send "echo '--- Final Status Check ---'\r"
expect "#"
send "systemctl status opi_gpio.service --no-pager -l\r"
expect "#"
send "systemctl status opi_web.service --no-pager -l\r"
expect "#"

send "exit\r"
expect eof
EXPECT_EOF

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "=== Remote Installation Completed Successfully ==="
    echo "Access the dashboard at: http://$TARGET_IP:5000"
    echo "API documentation at:    http://$TARGET_IP:8000/docs"
else
    echo ""
    echo "=== Remote Installation Failed (exit code: $EXIT_CODE) ==="
    echo "Check the output above for error details."
    exit 1
fi
