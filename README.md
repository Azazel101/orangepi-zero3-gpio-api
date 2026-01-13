# 🍊 Orange Pi Zero 3 GPIO API & Web Dashboard

A powerful, FastAPI-based REST API and a Loxone-inspired Web Dashboard to control the GPIO pins on an Orange Pi Zero 3 (v1.2). Designed for seamless smart home integration and easy network management.

## 🌟 Key Features
*   **Full GPIO Control**: Control pins as Inputs or Outputs via JSON API or Web UI.
*   **Loxone Inspired Dashboard**: A premium, mobile-friendly interface for manual control and system monitoring.
*   **Network Management**: Scan/Connect to Wi-Fi and configure Ethernet (Static/DHCP) directly from the browser.
*   **Loxone Integration**: Native XML templates for Virtual Inputs, Outputs, and System Stats.
*   **OTA Updates**: Secure "Safe Update" mechanism with automatic rollbacks.
*   **System Controls**: Remote Reboot and Shutdown capabilities.

## 🏗️ Project Structure
```text
opi_gpio_app/
├── main.py            # Hardware REST API (FastAPI - Port 8000)
├── web/               # Web Dashboard (Flask - Port 5000)
│   ├── app.py         # Dashboard Backend
│   ├── templates/     # Loxone-style HTML
│   └── static/        # CSS (Premium Styles) & JS (Real-time checks)
├── install_api.sh     # Main API installer
├── install_web.sh     # Web UI installer
├── update_safe.sh     # Secure OTA Update script
└── TODO.md            # Future Roadmap
```

## 🛠️ Installation
Run these commands on your Orange Pi as root:

### 1. Install Hardware API
```bash
cd /root/opi_gpio_app
chmod +x install_api.sh
./install_api.sh
```

### 2. Install Web Dashboard
```bash
chmod +x install_web.sh
./install_web.sh
```

## 📱 Web Dashboard
Access the premium interface via IP or mDNS hostname:
*   **mDNS**: `http://orangepizero3.local:5000` (Default hostname)
*   **IP**: `http://<ORANGE_PI_IP>:5000`

### 🔍 Discovery
If you don't know the hostname, you can find the device on your network:

**macOS (Terminal):**
```bash
dns-sd -B _http._tcp
# Look for "orangepizero3 GPIO Dashboard"
```

**Linux:**
```bash
avahi-browse -r _http._tcp
```

**Windows:**
Use a Bonjour browser or `nslookup -q=MDNS orangepizero3.local` if you know the name but need the IP.

Features include:
- **Real-time Status**: Live connection monitoring and system health.
- **Pin Grid**: Visual control of all GPIOs (Green = HIGH, Grey = LOW).
- **Network Setup**: Wi-Fi scanning and Ethernet configuration.
- **System Tab**: Access to Logs, OTA Updates, and Power Controls.

## 🔌 API Summary (Port 8000)
- **Status**: `GET /pins/status`
- **Toggle**: `POST /pins/toggle/<pin_num>`
- **Health**: `GET /health` (CPU Temp, RAM, Uptime)
- **Events**: `GET /events` (Queue of recent input triggers)
- **Logs**: `GET /logs` (JSON format)
- **Reboot**: `POST /system/reboot`
- **Shutdown**: `POST /system/shutdown`

## 💚 Loxone Integration
The system provides dynamically generated XML templates for **Loxone Config**. Download them via the Web Dashboard or API:
- `GET /loxone/template/inputs`
- `GET /loxone/template/outputs`
- `GET /loxone/template/stats`

## 📍 Hardware Pinout (26-pin Header)
![Orange Pi Zero 3 Pinout](pinout.png)

| Header Pin | Image Label | GPIO Bank | Offset |
| :--- | :--- | :--- | :--- |
| **Pin 3** | PH5 | PH | **229** |
| **Pin 5** | PH4 | PH | **228** |
| **Pin 7** | PC9 | PC | **73** (Inactive) |
| **Pin 11** | PC6 | PC | **70** |
| **Pin 12** | PC11 | PC | **75** |
| **Pin 13** | PC5 | PC | **69** |
| **Pin 15** | PC8 | PC | **72** |
| **Pin 16** | PC15 | PC | **79** |
| **Pin 18** | PC14 | PC | **78** |
| **Pin 19** | PH7 | PH | **231** |
| **Pin 21** | PH8 | PH | **232** |
| **Pin 22** | PC7 | PC | **71** |
| **Pin 23** | PH6 | PH | **230** |
| **Pin 24** | PH9 | PH | **233** |
| **Pin 26** | PC10 | PC | **74** |

## 🚀 Future Roadmap
See [TODO.md](TODO.md) for planned features like Web-based pin configuration, Dark Mode, and PWM support.

---
*Developed for Orange Pi Zero 3 v1.2*
