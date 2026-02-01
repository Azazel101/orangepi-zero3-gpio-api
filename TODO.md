# 📝 OPI GPIO Project - TODO & Roadmap

## 🚀 Proximity Features
Below are the proposed features to enhance the LoxIO Core API and Web Dashboard:

### 1. 🛠️ Web-Based Pin Configuration
- [x] Create a "Settings" page in the Web UI.
- [x] Implement API endpoints to update `gpio_config.json` via POST requests.
- [x] Add ability to change pin names, directions (Input/Output/Disabled), and bias settings (Pull-up/Pull-down).
- [x] Add hardware conflict validation and type checking.
- [x] Implement "Disabled" direction to release hardware pins without deleting them.

### 2. 🌙 Dark Mode & Live Charts
- [x] Implement a Dark/Light mode toggle using CSS variables.
- [x] Add live scrolling charts (using Chart.js or similar) for CPU Temperature and Load Average.
- [x] persistent theme selection via local storage.

### 3. 📈 Advanced Diagnostics (New)
- [x] Implement structured logging with millisecond removal.
- [x] Add automatic Log Rotation (1MB cap) to protect SD card.
- [x] Add Live Log streaming to the Web Dashboard.

### 4. ✨ UI/UX Premium Refinement
- [ ] **Glassmorphism Design**: Implement frosted glass effect, backdrop blurs, and premium gradients.
- [ ] **Real-time Engine**: Switch from polling to WebSockets for instant hardware-to-browser updates.
- [ ] **Native Feel (PWA)**: Add manifest.json and service workers for "Install to Home Screen" support.
- [ ] **Micro-Interactions**: Add Toast notifications, Skeleton loading screens, and smooth transitions.
- [ ] **Smart Organization**: Implement Pin Grouping/Rooms and a Global Search (Ctrl+K) bar.

### 5. 🔔 Webhooks & Notifications
- [ ] Create a configuration UI for Webhook URLs (Discord, Telegram, Slack).
- [ ] Implement a background task that triggers a webhook message when a specific input pin state changes.

### 6. ⚡ PWM (Dimming) Control
- [ ] Investigate `libgpiod` or kernel support for hardware/software PWM on Orange Pi Zero 3.
- [ ] Add a "Slider" component to the UI for PWM duty cycle control.
- [ ] Add Loxone Virtual Output support for analog (0-100%) values.

### 7. 🔒 Access Security
- [ ] Implement a simple Login page for the Web Dashboard.
- [ ] Add API Key authentication for the hardware API.
- [ ] Multi-user support or simple "Admin" password lock.

---
*Last updated: 2026-01-29*
