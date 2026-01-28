# LoxIO Discovery Tool for Windows
# (c) 2026 RS Soft

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$Form = New-Object System.Windows.Forms.Form
$Form.Text = "LoxIO Network Discovery"
$Form.Size = New-Object System.Drawing.Size(400, 500)
$Form.StartPosition = "CenterScreen"
$Form.BackColor = "#F4F4F4"
$Form.FormBorderStyle = "FixedDialog"
$Form.MaximizeBox = $false

$Title = New-Object System.Windows.Forms.Label
$Title.Text = "Available LoxIO Devices"
$Title.Font = New-Object System.Drawing.Font("Lexend", 14, [System.Drawing.FontStyle]::Bold)
$Title.Location = New-Object System.Drawing.Point(20, 20)
$Title.Size = New-Object System.Drawing.Size(350, 30)
$Title.ForeColor = "#3A4045"
$Form.Controls.Add($Title)

$DeviceList = New-Object System.Windows.Forms.ListBox
$DeviceList.Location = New-Object System.Drawing.Point(20, 60)
$DeviceList.Size = New-Object System.Drawing.Size(340, 300)
$DeviceList.Font = New-Object System.Drawing.Font("Segoe UI", 10)
$Form.Controls.Add($DeviceList)

$BtnSearch = New-Object System.Windows.Forms.Button
$BtnSearch.Text = "Search"
$BtnSearch.Location = New-Object System.Drawing.Point(20, 380)
$BtnSearch.Size = New-Object System.Drawing.Size(160, 45)
$BtnSearch.BackColor = "#3A4045"
$BtnSearch.ForeColor = "White"
$BtnSearch.FlatStyle = "Flat"
$BtnSearch.Font = New-Object System.Drawing.Font("Segoe UI", 10, [System.Drawing.FontStyle]::Bold)
$Form.Controls.Add($BtnSearch)

$BtnOpen = New-Object System.Windows.Forms.Button
$BtnOpen.Text = "Open Dashboard"
$BtnOpen.Location = New-Object System.Drawing.Point(190, 380)
$BtnOpen.Size = New-Object System.Drawing.Size(170, 45)
$BtnOpen.BackColor = "#69C350"
$BtnOpen.ForeColor = "White"
$BtnOpen.FlatStyle = "Flat"
$BtnOpen.Font = New-Object System.Drawing.Font("Segoe UI", 10, [System.Drawing.FontStyle]::Bold)
$BtnOpen.Enabled = $false
$Form.Controls.Add($BtnOpen)

$Status = New-Object System.Windows.Forms.Label
$Status.Text = "Scanning network..."
$Status.Location = New-Object System.Drawing.Point(20, 435)
$Status.Size = New-Object System.Drawing.Size(340, 20)
$Status.Font = New-Object System.Drawing.Font("Segoe UI", 8)
$Form.Controls.Add($Status)

# Discovery Logic
function Start-Discovery {
    $DeviceList.Items.Clear()
    $Status.Text = "Scanning network..."
    
    try {
        # Try to resolve mDNS services via native Windows API
        $services = Resolve-DnsName -Name "_http._tcp.local" -Type PTR -ErrorAction SilentlyContinue
        
        foreach ($svc in $services) {
            $name = $svc.NameHost
            if ($name -like "*LoxIO*") {
                # Clean name: "LoxIO Core LoxIO-XXXXX._http._tcp.local"
                $displayName = $name.Split('.')[0]
                $DeviceList.Items.Add($displayName)
            }
        }
    } catch {
        $Status.Text = "Discovery failed. mDNS might be disabled."
    }

    if ($DeviceList.Items.Count -eq 0) {
        $Status.Text = "No devices found."
    } else {
        $Status.Text = "Found $($DeviceList.Items.Count) device(s)."
    }
}

$DeviceList.Add_SelectedIndexChanged({
    $BtnOpen.Enabled = $DeviceList.SelectedItem -ne $null
})

$BtnOpen.Add_Click({
    $selected = $DeviceList.SelectedItem
    # Extract Hostname
    if ($selected -match "LoxIO-[A-Z0-9]*") {
        $hostname = $matches[0]
        $url = "http://$hostname.local:5000"
        [System.Diagnostics.Process]::Start($url)
    }
})

$BtnSearch.Add_Click({
    Start-Discovery
})

# Run discovery on start
$Form.Add_Shown({ Start-Discovery })

$Form.ShowDialog()
