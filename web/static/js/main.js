// Main JS for OPI GPIO Web UI
console.log("OPI GPIO Web Loaded");

async function checkConnection() {
    const statusContainer = document.getElementById('connection-status');
    if (!statusContainer) return;

    try {
        const response = await fetch('/api/health');
        const data = await response.json();

        if (data.status === 'healthy') {
            statusContainer.innerHTML = '<span class="badge badge-success"><i class="fas fa-check-circle"></i> Connected</span>';
        } else {
            statusContainer.innerHTML = '<span class="badge badge-error"><i class="fas fa-exclamation-triangle"></i> API Issue</span>';
        }
    } catch (e) {
        statusContainer.innerHTML = '<span class="badge badge-error"><i class="fas fa-plug"></i> Connection List</span>';
    }
}

// Check every 5 seconds
setInterval(checkConnection, 5000);
// Initial check
checkConnection();

// Theme Toggle
const themeToggle = document.getElementById('theme-toggle');
if (themeToggle) {
    themeToggle.addEventListener('click', () => {
        const isDark = document.body.classList.toggle('dark-mode');
        localStorage.setItem('theme', isDark ? 'dark' : 'light');

        // Update Icon
        const icon = themeToggle.querySelector('i');
        if (isDark) {
            icon.classList.replace('fa-moon', 'fa-sun');
        } else {
            icon.classList.replace('fa-sun', 'fa-moon');
        }

        // Notify charts or other components
        window.dispatchEvent(new CustomEvent('themeChanged', { detail: { theme: isDark ? 'dark' : 'light' } }));
    });
}
