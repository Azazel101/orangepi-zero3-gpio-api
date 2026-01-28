from flask import Flask, render_template, request, jsonify, redirect, url_for
import requests
import os

app = Flask(__name__)

# The GPIO API base URL
API_BASE_URL = os.environ.get("GPIO_API_URL", "http://192.168.1.29:8000")

def api_get(endpoint):
    try:
        response = requests.get(f"{API_BASE_URL}{endpoint}", timeout=10)
        return response.json()
    except Exception as e:
        return {"error": str(e)}

def api_post(endpoint, data=None):
    try:
        response = requests.post(f"{API_BASE_URL}{endpoint}", json=data, timeout=10)
        return response.json()
    except Exception as e:
        return {"error": str(e)}

@app.route('/')
def index():
    health = api_get("/health")
    net_status = api_get("/network/status")
    pins = api_get("/pins/status")
    
    # Defensive structures in case of API failure
    if "error" in health:
        health = {"system_stats": {"cpu_temp_c": 0, "ram": {"percent": 0}}, "board_info": {"uptime": "Unknown", "hostname": "Unknown", "os": "Unknown", "kernel": "Unknown"}}
    if "error" in net_status:
        net_status = {"ethernet": {"active": False}, "wifi": {"active": False}, "main_ip": "Offline"}
    if "error" in pins or not isinstance(pins, list):
        pins = []

    # Calculate counts
    io_counts = {"input": 0, "output": 0}
    for pin in pins:
        if pin.get('direction') in io_counts:
            io_counts[pin['direction']] += 1
                
    return render_template('index.html', 
                          health=health, 
                          net_status=net_status, 
                          io_counts=io_counts)

@app.route('/gpio')
def gpio_page():
    pins = api_get("/pins/status")
    if "error" in pins or not isinstance(pins, list):
        pins = []
    return render_template('gpio.html', pins=pins)

@app.route('/network')
def network_page():
    status = api_get("/network/status")
    if "error" in status:
        status = {"ethernet": {"active": False}, "wifi": {"active": False}, "main_ip": "Offline"}
    return render_template('network.html', status=status)

@app.route('/system')
def system_page():
    health = api_get("/health")
    if "error" in health:
        health = {"system_stats": {"cpu_temp_c": 0, "ram": {"percent": 0}}, "board_info": {"uptime": "Unknown", "hostname": "Unknown", "os": "Unknown", "kernel": "Unknown"}}
    update_info = api_get("/update/check")
    if "error" in update_info:
        update_info = {"local_hash": "Unknown", "update_available": False, "error": True}
    return render_template('system.html', health=health, update_info=update_info)

# API Proxies for AJAX calls
@app.route('/api/pins/toggle/<int:pin_num>', methods=['POST'])
def toggle_pin(pin_num):
    return jsonify(api_post(f"/pins/toggle/{pin_num}"))

@app.route('/api/pins/status')
def get_pins_status():
    return jsonify(api_get("/pins/status"))

@app.route('/api/network/scan')
def scan_wifi():
    return jsonify(api_get("/network/scan"))

@app.route('/api/network/connect', methods=['POST'])
def connect_wifi():
    data = request.json
    return jsonify(api_post("/network/connect", data))

@app.route('/api/network/ethernet', methods=['POST'])
def config_ethernet():
    data = request.json
    return jsonify(api_post("/network/ethernet", data))

@app.route('/api/health')
def get_health():
    return jsonify(api_get("/health"))

@app.route('/api/stats/history')
def get_stats_history():
    return jsonify(api_get("/stats/history"))

@app.route('/api/update/ota', methods=['POST'])
def trigger_update():
    return jsonify(api_post("/update/ota"))

@app.route('/api/system/reboot', methods=['POST'])
def trigger_reboot():
    return jsonify(api_post("/system/reboot"))

@app.route('/api/system/shutdown', methods=['POST'])
def trigger_shutdown():
    return jsonify(api_post("/system/shutdown"))

@app.route('/api/update/zip', methods=['POST'])
def update_zip():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    
    try:
        files = {'file': (file.filename, file.stream, file.mimetype)}
        response = requests.post(f"{API_BASE_URL}/update/zip", files=files, timeout=30)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/loxone/download/<type>')
def download_loxone_template(type):
    # Mapping to actual hardware API endpoints
    endpoints = {
        "inputs": "/loxone/template/inputs",
        "outputs": "/loxone/template/outputs",
        "stats": "/loxone/template/stats"
    }
    
    if type not in endpoints:
        return "Invalid template type", 404
        
    try:
        response = requests.get(f"{API_BASE_URL}{endpoints[type]}", timeout=10)
        return response.text, 200, {
            'Content-Type': 'application/xml',
            'Content-Disposition': f'attachment; filename=loxio_{type}.xml'
        }
    except Exception as e:
        return f"Error downloading template: {str(e)}", 500

@app.route('/api/logs')
def get_logs():
    try:
        response = requests.get(f"{API_BASE_URL}/logs", timeout=10)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"logs": [f"Error fetching logs: {str(e)}"]})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
