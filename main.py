import json
import gpiod
import os
import asyncio
from datetime import timedelta
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel
from typing import List, Dict, Optional
from gpiod.line import Direction, Value, Edge, Bias
import platform
import socket
from datetime import timedelta
from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles
import subprocess

# Dictionary to hold the requested GPIO line requests
# Key: (chip_path, line_offset), Value: gpiod.LineRequest
line_requests = {}
# Mapping: pin_num -> (chip_path, line_offset)
pin_mapping = {}

CONFIG_FILE = "gpio_config.json"
LOG_FILE = "app.log"

class PinState(BaseModel):
    pin_num: int
    state: int  # 0 or 1

class WifiConnect(BaseModel):
    ssid: str
    password: str

class EthernetConfig(BaseModel):
    method: str  # "auto" (DHCP) or "manual" (Static)
    ip: Optional[str] = None
    gateway: Optional[str] = None
    dns: Optional[str] = None

# Background task info
interrupt_task = None
event_queue = asyncio.Queue()

async def monitor_interrupts():
    """Background task to poll for GPIO edge events."""
    print("Interrupt monitor: Task started")
    while True:
        try:
            for (chip_path, line_offset), req in line_requests.items():
                # We only check for events on lines that were requested with edge detection
                # In this app, we requested ALL inputs with edge detection
                try:
                    # Use a very short timeout to avoid blocking the event loop
                    if req.wait_edge_events(timeout=0.01):
                        events = req.read_edge_events()
                        for event in events:
                            pin_num = next((k for k, v in pin_mapping.items() if v == (chip_path, line_offset)), "unknown")
                            msg = f"Interrupt on Pin {pin_num}: {'Rising' if event.event_type == Edge.RISING else 'Falling'}"
                            print(msg)
                            with open(LOG_FILE, "a") as f:
                                f.write(f"INFO: {msg}\n")
                            await event_queue.put({
                                "pin": pin_num, 
                                "event": "Rising" if event.event_type == Edge.RISING else "Falling",
                                "timestamp": str(event.timestamp_ns)
                            })
                except Exception as e:
                    # Not all lines support events (e.g. outputs)
                    pass
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            print("Interrupt monitor: Task cancelled")
            break
        except Exception as e:
            print(f"Interrupt monitor error: {e}")
            await asyncio.sleep(1)

def init_gpios():
    print("Initialising GPIOs (v2 API - Input/Interrupt Mode)...")
    if not os.path.exists(CONFIG_FILE):
        print(f"Error: {CONFIG_FILE} not found.")
        return

    try:
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
    except Exception as e:
        print(f"Error loading config: {e}")
        return

    for pin_cfg in config.get("pins", []):
        pin_num = pin_cfg.get("num")
        chip_num = pin_cfg.get("chip")
        line_offset = pin_cfg.get("line")
        direction_str = pin_cfg.get("direction", "output").lower()
        bias_str = pin_cfg.get("bias", "none").lower()
        chip_path = f"/dev/gpiochip{chip_num}"
        
        pin_mapping[pin_num] = (chip_path, line_offset)
        
        # Mapping configuration
        dir_val = Direction.OUTPUT if direction_str == "output" else Direction.INPUT
        
        bias_val = Bias.AS_IS
        if bias_str == "pull-up":
            bias_val = Bias.PULL_UP
        elif bias_str == "pull-down":
            bias_val = Bias.PULL_DOWN
        elif bias_str == "disabled":
            bias_val = Bias.DISABLED

        # Enable edge detection for all input pins
        edge_val = Edge.BOTH if dir_val == Direction.INPUT else Edge.NONE
        
        try:
            settings = gpiod.LineSettings(
                direction=dir_val,
                bias=bias_val,
                edge_detection=edge_val
            )
            
            if dir_val == Direction.OUTPUT:
                settings.output_value = Value.INACTIVE

            req = gpiod.request_lines(
                chip_path,
                consumer=f"fastapi-pin-{pin_num}",
                config={line_offset: settings}
            )
            line_requests[(chip_path, line_offset)] = req
            print(f"Successfully requested Pin {pin_num} ({direction_str}) on {chip_path}")
        except Exception as e:
            msg = f"Failed to request Pin {pin_num} ({direction_str}) on {chip_path}: {e}"
            print(msg)
            with open(LOG_FILE, "a") as f:
                f.write(f"ERROR: {msg}\n")

@asynccontextmanager
async def lifespan(app: FastAPI):
    global interrupt_task
    # Startup
    init_gpios()
    interrupt_task = asyncio.create_task(monitor_interrupts())
    yield
    # Shutdown
    print("Cleaning up GPIOs...")
    if interrupt_task:
        interrupt_task.cancel()
        try:
            await interrupt_task
        except asyncio.CancelledError:
            pass
    for req in line_requests.values():
        try:
            req.release()
        except:
            pass
    line_requests.clear()

app = FastAPI(
    title="Orange Pi Zero 3 GPIO API (v2)",
    description="""
A FastAPI application with Input and Interrupt support for Orange Pi Zero 3.

![Orange Pi Zero 3 Pinout](/static/pinout.png)

### Mapping Table (Orange Pi Zero 3 v1.2)
| Header Pin | Image Label | GPIO Bank | Line Offset |
| :--- | :--- | :--- | :--- |
| **Pin 3** | PH5 | PH | **229** |
| **Pin 5** | PH4 | PH | **228** |
| **Pin 7** | PC9 | PC | **73** |
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
    """,
    version="1.2.0",
    lifespan=lifespan
)

app.mount("/static", StaticFiles(directory="/root/opi_gpio_app"), name="static")

@app.get("/")
async def root():
    return {
        "message": "Orange Pi GPIO API with Input/Interrupt Support",
        "active_pins": list(pin_mapping.keys())
    }

def get_system_info():
    info = {
        "board": "unknown",
        "os": "unknown",
        "kernel": platform.release(),
        "arch": platform.machine(),
        "uptime": "unknown",
        "load_avg": [],
        "ram": {"total": 0, "available": 0, "percent": 0}
        #"disk": {"total": 0, "used": 0, "free": 0, "percent": 0}
    }

    # Board Info
    if os.path.exists("/etc/armbian-release"):
        try:
            with open("/etc/armbian-release", "r") as f:
                for line in f:
                    if line.startswith("BOARD_NAME="):
                        info["board"] = line.split("=")[1].strip().strip('"')
                        break
        except: pass

    # OS Info
    if os.path.exists("/etc/os-release"):
        try:
            with open("/etc/os-release", "r") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        info["os"] = line.split("=")[1].strip().strip('"')
                        break
        except: pass

    # Uptime
    if os.path.exists("/proc/uptime"):
        try:
            with open("/proc/uptime", "r") as f:
                uptime_seconds = float(f.readline().split()[0])
                info["uptime"] = str(timedelta(seconds=int(uptime_seconds)))
        except: pass

    # Load Average
    if os.path.exists("/proc/loadavg"):
        try:
            with open("/proc/loadavg", "r") as f:
                info["load_avg"] = [float(x) for x in f.read().split()[:3]]
        except: pass

    # RAM Usage
    if os.path.exists("/proc/meminfo"):
        try:
            mem = {}
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        name = parts[0].strip()
                        val = int(parts[1].split()[0])
                        mem[name] = val
            total = mem.get("MemTotal", 0)
            free = mem.get("MemFree", 0)
            buffers = mem.get("Buffers", 0)
            cached = mem.get("Cached", 0)
            available = free + buffers + cached
            info["ram"] = {
                "total_mb": round(total / 1024, 1),
                "available_mb": round(available / 1024, 1),
                "percent": round(100 * (1 - available / total), 1) if total > 0 else 0
            }
        except: pass

    return info

@app.get("/health")
async def health():
    # CPU temperature
    cpu_temp = "unknown"
    try:
        if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                cpu_temp = int(f.read().strip()) / 1000.0
    except: pass

    sys_info = get_system_info()

    return {
        "status": "healthy",
        "board_info": {
            "name": sys_info["board"],
            "os": sys_info["os"],
            "kernel": sys_info["kernel"],
            "arch": sys_info["arch"],
            "uptime": sys_info["uptime"]
        },
        "system_stats": {
            "cpu_temp_c": cpu_temp,
            "load_avg": sys_info["load_avg"],
            "ram": sys_info["ram"]
          #  "disk": sys_info["disk"]
        },
        "gpio_status": {
            "initialized": len(line_requests) > 0,
            "claimed_pins_count": len(line_requests),
            "interrupt_monitor_running": interrupt_task is not None and not interrupt_task.done()
        }
    }

@app.get("/pins/status")
async def get_status():
    status = []
    if not os.path.exists(CONFIG_FILE):
        raise HTTPException(status_code=404, detail="Config missing")
    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)
    for pin_cfg in config["pins"]:
        pin_num = pin_cfg["num"]
        mapping = pin_mapping.get(pin_num)
        current_val = -1
        is_active = False
        
        if mapping and mapping in line_requests:
            is_active = True
            try:
                val = line_requests[mapping].get_value(mapping[1])
                current_val = 1 if val == Value.ACTIVE else 0
            except Exception as e:
                pass
        
        status.append({
            **pin_cfg,
            "active": is_active,
            "current_state": current_val
        })
    return status

@app.get("/events")
async def get_events():
    """Returns all queued interrupt events and clears the queue."""
    events = []
    while not event_queue.empty():
        events.append(event_queue.get_nowait())
    return {"events": events}

@app.post("/pins/set")
async def set_pin(request: Request):
    try:
        # Loxone sends Content-Type: text/plain, which FastAPI rejects for Pydantic models.
        # We manually parse the body to support this behavior.
        body_bytes = await request.body()
        if not body_bytes:
             raise HTTPException(status_code=400, detail="Empty body")
             
        try:
            body_json = json.loads(body_bytes)
            data = PinState(**body_json)
        except json.JSONDecodeError:
             raise HTTPException(status_code=422, detail="Invalid JSON format")
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Unprocessable Entity: {str(e)}")

    mapping = pin_mapping.get(data.pin_num)
    if not mapping or mapping not in line_requests:
        raise HTTPException(status_code=404, detail="Pin not active")
    
    val = Value.ACTIVE if data.state == 1 else Value.INACTIVE
    try:
        line_requests[mapping].set_value(mapping[1], val)
        return {"pin_num": data.pin_num, "state": data.state, "status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/pins/toggle/{pin_num}")
async def toggle_pin(pin_num: int):
    mapping = pin_mapping.get(pin_num)
    if not mapping or mapping not in line_requests:
        raise HTTPException(status_code=404, detail="Pin not active")
    
    try:
        current = line_requests[mapping].get_value(mapping[1])
        new_state = Value.INACTIVE if current == Value.ACTIVE else Value.ACTIVE
        line_requests[mapping].set_value(mapping[1], new_state)
        return {"pin_num": pin_num, "state": 1 if new_state == Value.ACTIVE else 0, "status": "toggled"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/logs")
async def get_logs(lines: int = 100):
    if not os.path.exists(LOG_FILE):
        return {"message": "Log file not found"}
    try:
        with open(LOG_FILE, "r") as f:
            content = f.readlines()
            return {"logs": content[-lines:]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/loxone/status", response_class=PlainTextResponse)
async def get_loxone_status():
    """PlainText status for Loxone parsing (Pin <NUM>=<VAL>)"""
    output = []
    
    # Reload config to be safe
    try:
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
    except:
        return "Error loading config"

    for pin_cfg in config["pins"]:
        pin_num = pin_cfg["num"]
        mapping = pin_mapping.get(pin_num)
        current_val = 0
        
        # If it's active, get real value
        if mapping and mapping in line_requests:
            try:
                val = line_requests[mapping].get_value(mapping[1])
                current_val = 1 if val == Value.ACTIVE else 0
            except: pass
        
        output.append(f"Pin {pin_num}={current_val}")
    
    return "\n".join(output)

@app.get("/loxone/stats", response_class=PlainTextResponse)
async def get_loxone_stats():
    """PlainText system stats for Loxone parsing"""
    info = get_system_info()
    
    # CPU Temp
    cpu_temp = 0.0
    try:
        if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                cpu_temp = int(f.read().strip()) / 1000.0
    except: pass

    # Format specifically for Loxone Virtual Input parsing (Key=Value)
    output = [
        f"Temp={cpu_temp:.1f}",
        f"Load={info['load_avg'][0] if info['load_avg'] else 0.0}",
        f"RamPercent={info['ram']['percent']}",
        #f"DiskPercent={info['disk']['percent']}",
        # Uptime in seconds for simpler parsing if needed, but we keep human readable in health
        # Parsing "1 day, 2:30:00" is hard in Loxone. Let's provide uptime in hours.
        # Rework uptime to float hours
    ]
    
    # Calculate Uptime Hours
    uptime_hours = 0.0
    if os.path.exists("/proc/uptime"):
        try:
            with open("/proc/uptime", "r") as f:
                uptime_seconds = float(f.readline().split()[0])
                uptime_hours = round(uptime_seconds / 3600.0, 2)
        except: pass
    output.append(f"UptimeHours={uptime_hours}")

    return "\n".join(output)

def get_ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

@app.get("/loxone/template/inputs", response_class=Response)
async def get_loxone_input_template():
    """Generate Loxone Virtual Input Template XML"""
    ip_addr = get_ip_address()
    base_url = f"http://{ip_addr}:8000"
    
    try:
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
    except:
        raise HTTPException(500, "Config error")

    # Structure based on user provided working example:
    # <VirtualInHttp ...> (The Connector)
    #   <VirtualInHttpCmd ...> (The Pin Command)
    # </VirtualInHttp>

    xml_lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<VirtualInHttp Title="OrangePi_Zero3_Inputs ({ip_addr})" Comment="Generated by API" Address="{base_url}/loxone/status" HintText="" PollingTime="1">',
        '  <Info templateType="2" minVersion="15050304"/>'
    ]

    for pin_cfg in config["pins"]:
        # Only generate Virtual Inputs for pins explicitly configured as 'input'
        if pin_cfg.get("direction", "output").lower() == "input":
            pin_num = pin_cfg["num"]
            name = pin_cfg.get("name", str(pin_num))
            
            # Using the standard attributes from the user's weather example, adapted for digital pins
            xml_lines.append(
                f'  <VirtualInHttpCmd Title="Pin {pin_num} ({name})" Comment="" Check="Pin {pin_num}=\\v" '
                f'Signed="true" Analog="true" SourceValLow="0" DestValLow="0" SourceValHigh="1" DestValHigh="1" '
                f'DefVal="0" MinVal="0" MaxVal="1" Unit="" HintText=""/>'
            )

    xml_lines.append('</VirtualInHttp>')
    
    return Response(content="\n".join(xml_lines), media_type="application/xml", headers={"Content-Disposition": 'attachment; filename="OrangePi_Inputs.xml"'})

@app.get("/loxone/template/outputs", response_class=Response)
async def get_loxone_output_template():
    """Generate Loxone Virtual Output Template XML for Output pins"""
    ip_addr = get_ip_address()
    base_url = f"http://{ip_addr}:8000"
    
    try:
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
    except:
        raise HTTPException(500, "Config error")

    # Structure based on user provided working example
    # Note: Loxone Config imports usually require utf-8 BOM or specific structure.
    # The user provided a single <VirtualOut> block. For template import, it usually needs to be wrapped.
    # We will wrap it in a root element but keep the internal structure exactly as requested.
    
    xml_lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<VirtualOut HintText="" Title="OrangePi_Zero3_Outputs ({ip_addr})" Comment="Generated by API" Address="{base_url}" CmdInit="" CloseAfterSend="true" CmdSep=";">',
        '  <Info templateType="3" minVersion="16021217"/>'
    ]

    for pin_cfg in config["pins"]:
        # Only include pins configured as output
        if pin_cfg.get("direction", "output").lower() == "output":
            pin_num = pin_cfg["num"]
            name = pin_cfg.get("name", str(pin_num))
            
            # Using POST for our API, adapting the user's GET example to our POST endpoints
            # CmdOn/CmdOff in user example are logic inverted (off/on -> off/on)
            # We map: Loxone ON -> API High (1), Loxone OFF -> API Low (0)
            
            # Since Loxone VirtualOutCmd attributes for POST are specific:
            # CmdOn: URL path, CmdOnPost: Body data
            
            on_body = json.dumps({"pin_num": pin_num, "state": 1}).replace('"', '&quot;')
            off_body = json.dumps({"pin_num": pin_num, "state": 0}).replace('"', '&quot;')
            
            # Using Method="POST" (Standard Loxone attribute for HTTP Method is explicitly supported in newer versions, 
            # or implicitly via CmdOn/CmdOnPost). 
            # The user's example used GET. To support POST properly with this legacy-style XML:
            # CmdOnMethod="POST"
            
            xml_lines.append(
                f'  <VirtualOutCmd Title="Pin {pin_num} ({name})" Comment="" '
                f'CmdOnMethod="POST" CmdOffMethod="POST" '
                f'CmdOn="/pins/set" CmdOnHTTP="" CmdOnPost="{on_body}" '
                f'CmdOff="/pins/set" CmdOffHTTP="" CmdOffPost="{off_body}" '
                f'CmdAnswer="" Analog="false" Repeat="0" RepeatRate="0" HintText=""/>'
            )

    xml_lines.append('</VirtualOut>')
    
    return Response(content="\n".join(xml_lines), media_type="application/xml", headers={"Content-Disposition": 'attachment; filename="OrangePi_Outputs.xml"'})

@app.get("/loxone/template/stats", response_class=Response)
async def get_loxone_stats_template():
    """Generate Loxone Virtual Input Template XML for System Stats"""
    ip_addr = get_ip_address()
    base_url = f"http://{ip_addr}:8000"
    
    xml_lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<VirtualInHttp Title="OrangePi_Zero3_Stats ({ip_addr})" Comment="Generated by API" Address="{base_url}/loxone/stats" HintText="" PollingTime="60">',
        '  <Info templateType="2" minVersion="15050304"/>'
    ]
    
    # helper for cmd
    def add_cmd(title, check, unit):
        xml_lines.append(
            f'  <VirtualInHttpCmd Title="{title}" Comment="" Check="{check}=\\v" '
            f'Signed="true" Analog="true" SourceValLow="0" DestValLow="0" SourceValHigh="100" DestValHigh="100" '
            f'DefVal="0" MinVal="0" MaxVal="10000" Unit="{unit}" HintText=""/>'
        )

    add_cmd("CPU Temperature", "Temp", "ºC")
    add_cmd("CPU Load (1m)", "Load", "")
    add_cmd("RAM Usage", "RamPercent", "%")
    add_cmd("Uptime", "UptimeHours", "h")

    xml_lines.append('</VirtualInHttp>')
    
    return Response(content="\n".join(xml_lines), media_type="application/xml", headers={"Content-Disposition": 'attachment; filename="OrangePi_Stats.xml"'})

@app.get("/update/check")
async def check_update():
    """Check if update is available"""
    try:
        # Fetch latest changes without applying
        subprocess.run(["git", "fetch", "origin", "main"], cwd="/root/opi_gpio_app", check=True)
        
        # Get local and remote hashes
        local_hash = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd="/root/opi_gpio_app", text=True).strip()
        remote_hash = subprocess.check_output(["git", "rev-parse", "origin/main"], cwd="/root/opi_gpio_app", text=True).strip()
        
        update_available = local_hash != remote_hash
        return {
            "update_available": update_available,
            "local_hash": local_hash,
            "remote_hash": remote_hash
        }
    except Exception as e:
        return {"error": str(e), "update_available": False}

@app.post("/update/ota")
async def ota_update(force: bool = False):
    """Trigger OTA update via safe shell script"""
    try:
        # First check if update is needed (unless forced)
        if not force:
             subprocess.run(["git", "fetch", "origin", "main"], cwd="/root/opi_gpio_app", check=True)
             local = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd="/root/opi_gpio_app", text=True).strip()
             remote = subprocess.check_output(["git", "rev-parse", "origin/main"], cwd="/root/opi_gpio_app", text=True).strip()
             if local == remote:
                 return {"status": "skipped", "message": "Already up to date"}

        # Verification check: Ensure script exists
        if not os.path.exists("/root/opi_gpio_app/update_safe.sh"):
             return {"status": "error", "message": "Safety script missing. Cannot update safely."}

        # Run the safe update script in non-blocking way (fire and forget)
        # We use start_new_session to detach prompts/signals so it survives restart
        subprocess.Popen(
            ["/bin/bash", "/root/opi_gpio_app/update_safe.sh"],
            cwd="/root/opi_gpio_app",
            start_new_session=True
        )
        
        return {"status": "initiated", "message": "Safe update started. The API will restart momentarily. Check logs for result."}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Network Management Endpoints ---

@app.get("/network/status")
async def network_status():
    """Get comprehensive network status for both WiFi and Ethernet"""
    try:
        # Force English output for nmcli
        env = os.environ.copy()
        env["LANG"] = "C"
        
        # Get device status
        # Format: DEVICE:TYPE:STATE:CONNECTION
        dev_cmd = subprocess.run(
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev"],
            capture_output=True, text=True, env=env
        )
        
        status = {
            "main_ip": get_ip_address(),
            "wifi": {"active": False, "ssid": None, "device": None, "signal_percent": 0, "state": "unknown"},
            "ethernet": {"active": False, "connection": None, "device": None, "state": "unknown"}
        }

        if dev_cmd.returncode == 0:
            for line in dev_cmd.stdout.strip().split('\n'):
                parts = line.split(':')
                if len(parts) < 3: continue
                device, dev_type, state = parts[0], parts[1], parts[2]
                conn_name = parts[3] if len(parts) > 3 else None

                # Normalize states like "connected (externally)" or "unmanaged"
                is_connected = "connected" in state.lower()
                
                if dev_type == "wifi":
                    status["wifi"]["device"] = device
                    status["wifi"]["state"] = state
                    if is_connected:
                        status["wifi"]["active"] = True
                        status["wifi"]["ssid"] = conn_name
                        
                        # Get signal
                        sig_cmd = subprocess.run(
                             ["nmcli", "-t", "-f", "SSID,SIGNAL", "dev", "wifi"],
                             capture_output=True, text=True, env=env
                        )
                        if sig_cmd.stdout:
                             for sig_line in sig_cmd.stdout.split('\n'):
                                 if sig_line.startswith(str(conn_name) + ":"):
                                     try:
                                         status["wifi"]["signal_percent"] = int(sig_line.split(":")[1])
                                     except: pass
                                     break
                
                elif dev_type == "ethernet":
                    status["ethernet"]["device"] = device
                    status["ethernet"]["state"] = state
                    if is_connected or state == "unmanaged":
                        # If unmanaged but we have a main_ip, it might be the ethernet
                        status["ethernet"]["active"] = True
                        status["ethernet"]["connection"] = conn_name if conn_name else "System Managed"

        return status
    except Exception as e:
         raise HTTPException(status_code=500, detail=str(e))

@app.get("/network/scan")
async def network_scan():
    """Scan for available Wi-Fi networks"""
    try:
        # Rescan first
        subprocess.run(["nmcli", "dev", "wifi", "rescan"], capture_output=True)
        
        # Get list
        # SSID,SIGNAL,SECURITY
        cmd = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,BARS", "dev", "wifi", "list"],
            capture_output=True, text=True
        )
        
        networks = []
        seen_ssids = set()
        
        if cmd.returncode == 0:
            for line in cmd.stdout.split('\n'):
                if not line: continue
                # nmcli escapes colons with backslashes in SSID, handling that strictly is hard with just split.
                # Assuming simple SSIDs for now or last fields.
                # Format: SSID:SIGNAL:SECURITY:BARS
                
                parts = line.split(':')
                if len(parts) >= 3:
                     # Join all parts except last 3 as SSID (in case SSID contains colon)
                     ssid = ":".join(parts[:-3])
                     if not ssid: continue # hidden SSID
                     
                     if ssid not in seen_ssids:
                         networks.append({
                             "ssid": ssid,
                             "signal": int(parts[-3]),
                             "security": parts[-2],
                             "bars": parts[-1]
                         })
                         seen_ssids.add(ssid)
        
        return {"networks": sorted(networks, key=lambda x: x['signal'], reverse=True)}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/network/connect")
async def network_connect(data: WifiConnect):
    """Connect to a Wi-Fi network"""
    # This is a blocking operation and might take time.
    try:
        # nmcli dev wifi connect <SSID> password <PASSWORD>
        result = subprocess.run(
            ["nmcli", "dev", "wifi", "connect", data.ssid, "password", data.password],
            capture_output=True, text=True
        )
        
        if result.returncode == 0:
            return {"status": "success", "message": f"Connected to {data.ssid}", "details": result.stdout}
        else:
            return {"status": "error", "message": "Failed to connect", "details": result.stderr}
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/network/ethernet")
async def configure_ethernet(data: EthernetConfig):
    """Configure Ethernet (DHCP or Static IP)"""
    try:
        # Identify Ethernet Interface (usually eth0 or end0 on OrangePi)
        # We find the device with type 'ethernet'
        dev_cmd = subprocess.run(
            ["nmcli", "-t", "-f", "DEVICE,TYPE", "dev"],
            capture_output=True, text=True
        )
        eth_dev = None
        if dev_cmd.stdout:
            for line in dev_cmd.stdout.split('\n'):
                if ":ethernet" in line:
                    eth_dev = line.split(':')[0]
                    break
        
        if not eth_dev:
             return {"status": "error", "message": "No Ethernet device found"}

        # Connection Name usually 'Wired connection 1' or similar.
        # We will create/modify a connection named 'eth-config' for consistency
        con_name = "eth-config"
        
        # Check if connection exists
        check_con = subprocess.run(
            ["nmcli", "con", "show", con_name],
            capture_output=True
        )
        
        cmds = []
        if check_con.returncode != 0:
            # Create new connection
            cmds.append(["nmcli", "con", "add", "con-name", con_name, "ifname", eth_dev, "type", "ethernet"])
        
        # Configure Method
        if data.method == "auto":
             cmds.append(["nmcli", "con", "mod", con_name, "ipv4.method", "auto"])
             cmds.append(["nmcli", "con", "mod", con_name, "ipv4.addresses", ""])
             cmds.append(["nmcli", "con", "mod", con_name, "ipv4.gateway", ""])
             cmds.append(["nmcli", "con", "mod", con_name, "ipv4.dns", ""])
        elif data.method == "manual":
             if not data.ip or not data.gateway:
                 raise HTTPException(status_code=400, detail="IP and Gateway required for manual mode")
             
             # IP Format: 192.168.1.50/24
             ip_cidr = data.ip if "/" in data.ip else f"{data.ip}/24"
             
             cmds.append(["nmcli", "con", "mod", con_name, "ipv4.method", "manual"])
             cmds.append(["nmcli", "con", "mod", con_name, "ipv4.addresses", ip_cidr])
             cmds.append(["nmcli", "con", "mod", con_name, "ipv4.gateway", data.gateway])
             if data.dns:
                 cmds.append(["nmcli", "con", "mod", con_name, "ipv4.dns", data.dns])
        
        # Apply changes
        for cmd in cmds:
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                return {"status": "error", "message": f"Command failed: {' '.join(cmd)}", "details": r.stderr}

        # Bring Up Connection
        up_res = subprocess.run(["nmcli", "con", "up", con_name], capture_output=True, text=True)
        
        if up_res.returncode == 0:
             return {"status": "success", "message": f"Ethernet configured ({data.method})", "details": up_res.stdout}
        else:
             return {"status": "error", "message": "Failed to activate connection", "details": up_res.stderr}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
