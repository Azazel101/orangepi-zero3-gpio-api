#!/bin/bash

# LoxIO Discovery Tool for MacOS
# (c) 2026 RS Soft

echo "========================================"
echo "      LoxIO Network Discovery Tool"
echo "========================================"
echo "Scanning for LoxIO Core devices..."

# Create a temporary file for scan results
SCAN_FILE=$(mktemp)

# Browse for HTTP services for 3 seconds
# Output format: Timestamp A/R Flags if Domain ServiceType InstanceName
dns-sd -B _http._tcp local. > "$SCAN_FILE" &
SCAN_PID=$!
sleep 3
kill "$SCAN_PID" 2>/dev/null

# Filter for LoxIO devices and extract the instance names
# We look for lines containing "LoxIO" and extract the name (column 7+)
DEVICES=$(grep "LoxIO" "$SCAN_FILE" | awk '{for(i=7;i<=NF;i++) printf "%s%s", $i, (i==NF?"":" ")}' | sort | uniq)

rm -f "$SCAN_FILE"

if [ -z "$DEVICES" ]; then
    echo "No LoxIO devices found."
    osascript -e 'display alert "LoxIO Discovery" message "No LoxIO devices were found on your local network. Please ensure they are powered on and connected to the same Wi-Fi/Ethernet."'
    exit 0
fi

echo "Found devices:"
echo "$DEVICES"

# Use AppleScript to show a selection dialog
SELECTED=$(echo "$DEVICES" | osascript -e 'set theList to paragraphs of (do shell script "cat")' \
-e 'choose from list theList with title "LoxIO Discovery" with prompt "Select a LoxIO device to open the dashboard:" default items {item 1 of theList}' \
-e 'if result is false then return "CANCEL"' \
-e 'return result')

if [ "$SELECTED" != "CANCEL" ]; then
    # Convert name to .local address (Instance names are often same as hostname)
    # LoxIO Core LoxIO-XXXXX -> LoxIO-XXXXX.local
    HOSTNAME=$(echo "$SELECTED" | grep -o "LoxIO-[A-Z0-9]*")
    
    if [ -z "$HOSTNAME" ]; then
        # Fallback to direct name if regex fails
        HOSTNAME=$(echo "$SELECTED" | tr ' ' '-')
    fi
    
    URL="http://$HOSTNAME.local:5000"
    echo "Opening $URL..."
    open "$URL"
fi
