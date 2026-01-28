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
# The Instance Name starts at column 7 in dns-sd -B output
DEVICES=$(grep "LoxIO" "$SCAN_FILE" | awk '{ $1=$2=$3=$4=$5=$6=""; print $0 }' | sed 's/^[ \t]*//' | sort | uniq)

rm -f "$SCAN_FILE"

if [ -z "$DEVICES" ]; then
    echo "No LoxIO devices found."
    osascript -e 'display alert "LoxIO Discovery" message "No LoxIO devices were found on your local network. Please ensure they are powered on and connected to the same network."'
    exit 0
fi

echo "Found devices:"
echo "$DEVICES"

# Use AppleScript to show a selection dialog. We pass the list as arguments to avoid cat/pipe issues.
SELECTED=$(osascript <<EOT
    set deviceString to "$DEVICES"
    set theList to paragraphs of deviceString
    if (count of theList) is 0 then
        return "CANCEL"
    end if
    tell application "System Events"
        activate
        choose from list theList with title "LoxIO Discovery" with prompt "Select a LoxIO device to open the dashboard:" default items {item 1 of theList}
    end tell
    if result is false then
        return "CANCEL"
    else
        return item 1 of result
    end if
EOT
)

if [ "$SELECTED" != "CANCEL" ] && [ -n "$SELECTED" ]; then
    # Extract Hostname (look for LoxIO-XXXXX pattern)
    HOSTNAME=$(echo "$SELECTED" | grep -oEi "LoxIO-[A-Z0-9]+")
    
    if [ -z "$HOSTNAME" ]; then
        # Last resort fallback: use the first word or the whole name
        HOSTNAME=$(echo "$SELECTED" | awk '{print $1}')
    fi
    
    URL="http://$HOSTNAME.local:5000"
    echo "Opening $URL..."
    open "$URL"
fi
