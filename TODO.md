# 📝 OPI GPIO Project - TODO & Roadmap

## 🚀 Proximity Features
Below are the proposed features to enhance the Orange Pi GPIO API and Web Dashboard:

### 1. 🛠️ Web-Based Pin Configuration
- [ ] Create a "Settings" page in the Web UI.
- [ ] Implement API endpoints to update `gpio_config.json` via POST requests.
- [ ] Add ability to change pin names, directions (Input/Output), and bias settings (Pull-up/Pull-down).

### 2. 🌙 Dark Mode & Live Charts
- [ ] Implement a Dark/Light mode toggle using CSS variables.
- [ ] Add live scrolling charts (using Chart.js or similar) for CPU Temperature and Load Average.
- [ ] persistent theme selection via local storage.

### 3. 🔔 Webhooks & Notifications
- [ ] Create a configuration UI for Webhook URLs (Discord, Telegram, Slack).
- [ ] Implement a background task that triggers a webhook message when a specific input pin state changes.

### 4. ⚡ PWM (Dimming) Control
- [ ] Investigate `libgpiod` or kernel support for hardware/software PWM on Orange Pi Zero 3.
- [ ] Add a "Slider" component to the UI for PWM duty cycle control.
- [ ] Add Loxone Virtual Output support for analog (0-100%) values.

### 5. 🔒 Access Security
- [ ] Implement a simple Login page for the Web Dashboard.
- [ ] Add API Key authentication for the hardware API.
- [ ] Multi-user support or simple "Admin" password lock.

---
*Last updated: 2026-01-10*
