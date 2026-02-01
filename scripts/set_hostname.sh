#!/bin/bash
set -euo pipefail

# ============================================================================
# LoxIO Core - Unique Hostname Generator (Sunxi ChipID)
# Format: LoxIO-<10_CHAR_ID>
# ============================================================================

SYS_INFO="/sys/class/sunxi_info/sys_info"

log() {
    echo "[$(date '+%H:%M:%S')] $1"
}

error() {
    echo "[$(date '+%H:%M:%S')] ERROR: $1" >&2
    exit 1
}

# Check if running on Sunxi hardware
if [ ! -f "$SYS_INFO" ]; then
    error "$SYS_INFO not found. This script only works on Sunxi (Orange Pi) hardware."
fi

# Extract full chip ID
FULL_CHIP_ID=$(grep "sunxi_chipid" "$SYS_INFO" 2>/dev/null | awk '{print $3}' || echo "")

if [ -z "$FULL_CHIP_ID" ]; then
    error "Could not extract chipid from $SYS_INFO"
fi

# Generate 10 character unique ID using MD5 hash
UNIQUE_ID=$(echo -n "$FULL_CHIP_ID" | md5sum | cut -c1-10 | tr '[:lower:]' '[:upper:]')
NEW_HOSTNAME="LoxIO-$UNIQUE_ID"

log "Detected ChipID: $FULL_CHIP_ID"
log "Generated ID: $UNIQUE_ID"
log "Setting hostname to: $NEW_HOSTNAME"

# Set system hostname
if ! hostnamectl set-hostname "$NEW_HOSTNAME"; then
    error "Failed to set hostname via hostnamectl"
fi

# Backup /etc/hosts before modification
if [ -f /etc/hosts ]; then
    cp /etc/hosts /etc/hosts.bak
fi

# Update /etc/hosts (escape special chars in hostname for sed)
ESCAPED_HOSTNAME=$(printf '%s\n' "$NEW_HOSTNAME" | sed 's/[[\.*^$()+?{|]/\\&/g')
if ! sed -i "s/127.0.1.1.*/127.0.1.1\t$ESCAPED_HOSTNAME/g" /etc/hosts; then
    log "Warning: Failed to update /etc/hosts, restoring backup"
    [ -f /etc/hosts.bak ] && cp /etc/hosts.bak /etc/hosts
fi

# Verify the change
CURRENT_HOSTNAME=$(hostname)
if [ "$CURRENT_HOSTNAME" == "$NEW_HOSTNAME" ]; then
    log "SUCCESS: Hostname set to $NEW_HOSTNAME"
else
    log "Warning: Hostname mismatch - expected $NEW_HOSTNAME, got $CURRENT_HOSTNAME"
fi

log "Restart avahi-daemon to broadcast the new name: systemctl restart avahi-daemon"
