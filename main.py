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
import shutil
import platform
import socket
from datetime import timedelta
from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles

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
        "ram": {"total": 0, "available": 0, "percent": 0},
        "disk": {"total": 0, "used": 0, "free": 0, "percent": 0}
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

    # Disk Usage
    try:
        usage = shutil.disk_usage("/")
        info["disk"] = {
            "total_gb": round(usage.total / (2**30), 1),
            "used_gb": round(usage.used / (2**30), 1),
            "free_gb": round(usage.free / (2**30), 1),
            "percent": round(100 * usage.used / usage.total, 1)
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
            "ram": sys_info["ram"],
            "disk": sys_info["disk"]
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

    xml_lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<Templates>',
        f'  <VirtualInHttpTemplate Title="OrangePi_GPIO_Inputs ({ip_addr})" Comment="Generated by API" Icon="056">'
    ]

    for pin_cfg in config["pins"]:
        pin_num = pin_cfg["num"]
        name = pin_cfg.get("name", str(pin_num))
        # XML Entity escaping usually handled, but strictly we should escape.
        # Minimal escaping for "Name" just in case
        
        # Virtual Input for Loxone
        # Command recognition: Pin X=\v
        xml_lines.append(
            f'    <VirtualInHttp Name="Pin {pin_num} ({name})" URL="{base_url}/loxone/status" '
            f'PollingCycle="1" Command="Pin {pin_num}=\\v" />'
        )

    xml_lines.append('  </VirtualInHttpTemplate>')
    xml_lines.append('</Templates>')
    
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
        f'<VirtualOut HintText="" Title="OrangePi_Zero3_GPIO ({ip_addr})" Comment="Generated by API" Address="{base_url}" CmdInit="" CloseAfterSend="true" CmdSep=";">',
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
