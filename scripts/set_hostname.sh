#!/bin/bash

# Script to set a unique hostname based on Sunxi ChipID
# Format: LoxIO-<10_CHAR_ID>

SYS_INFO="/sys/class/sunxi_info/sys_info"

if [ ! -f "$SYS_INFO" ]; then
    echo "Error: $SYS_INFO not found. This script only works on Sunxi (Orange Pi) hardware."
    exit 1
fi

# Extract full chip ID
FULL_CHIP_ID=$(grep "sunxi_chipid" "$SYS_INFO" | awk '{print $3}')

if [ -z "$FULL_CHIP_ID" ]; then
    echo "Error: Could not extract chipid from $SYS_INFO"
    exit 1
fi

# Generate 10 character unique ID
# We use md5sum to ensure it's a consistent 10-char alphanumeric string derived from the chip ID
UNIQUE_ID=$(echo -n "$FULL_CHIP_ID" | md5sum | cut -c1-10 | tr '[:lower:]' '[:upper:]')

NEW_HOSTNAME="LoxIO-$UNIQUE_ID"

echo "Detected ChipID: $FULL_CHIP_ID"
echo "Generated ID: $UNIQUE_ID"
echo "Setting hostname to: $NEW_HOSTNAME"

# Set system hostname
hostnamectl set-hostname "$NEW_HOSTNAME"

# Update /etc/hosts to prevent sudo warnings
sed -i "s/127.0.1.1.*/127.0.1.1\t$NEW_HOSTNAME/g" /etc/hosts

echo "Success! Please reboot or restart avahi-daemon to broadcast the new name."
