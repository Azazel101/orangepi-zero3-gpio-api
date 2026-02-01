import json
import gpiod
import os
import asyncio
from datetime import timedelta
from fastapi import FastAPI, HTTPException, Request, File, UploadFile
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel
from typing import List, Dict, Optional
from gpiod.line import Direction, Value, Edge, Bias
import platform
import socket
from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles
import subprocess
import time
import shutil
from collections import deque
import logging
from logging.handlers import RotatingFileHandler

# =============================================================================
# CONFIGURATION CONSTANTS
# =============================================================================
STATS_COLLECT_INTERVAL_SEC = 10          # How often to collect system stats
STATS_HISTORY_MAX_POINTS = 360           # Max history points (360 * 10s = 1 hour)
INTERRUPT_POLL_INTERVAL_SEC = 0.1        # GPIO interrupt polling interval
INTERRUPT_EDGE_TIMEOUT_SEC = 0.01        # Edge event wait timeout
EVENT_QUEUE_MAX_SIZE = 1000              # Max queued GPIO events
LOG_MAX_BYTES = 1 * 1024 * 1024          # 1MB per log file
LOG_BACKUP_COUNT = 5                     # Number of log file backups
TASK_HEALTH_CHECK_INTERVAL_SEC = 30      # Background task health check interval

# =============================================================================
# GLOBAL STATE
# =============================================================================
# Dictionary to hold the requested GPIO line requests
# Key: (chip_path, line_offset), Value: gpiod.LineRequest
line_requests = {}
# Mapping: pin_num -> (chip_path, line_offset)
pin_mapping = {}
# Reverse mapping for efficient lookup: (chip_path, line_offset) -> pin_num
reverse_pin_mapping = {}

CONFIG_FILE = "gpio_config.json"
DEFAULT_CONFIG_FILE = "gpio_config.default.json"  # Template config (tracked in git)
APP_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(APP_DIR, "scripts")
LOG_FILE = os.path.join(APP_DIR, "app.log")

# Setup Structured Logging
logger = logging.getLogger("LoxIO")
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

# Rotating handler with configurable size and backup count
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Also log to console for development
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)
logger.addHandler(stream_handler)

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
stats_task = None
task_monitor_task = None
event_queue = asyncio.Queue(maxsize=EVENT_QUEUE_MAX_SIZE)
# Circular buffer for stats: [(timestamp, cpu_temp, load_1m), ...]
stats_history = deque(maxlen=STATS_HISTORY_MAX_POINTS)

async def monitor_stats():
    """Background task to collect system stats periodically."""
    logger.info("Stats monitor: Task started")
    while True:
        try:
            # CPU temperature
            cpu_temp = 0.0
            if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
                try:
                    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                        cpu_temp = int(f.read().strip()) / 1000.0
                except (IOError, ValueError) as e:
                    logger.warning(f"Failed to read CPU temperature: {e}")

            # Load Average
            load_1m = 0.0
            if os.path.exists("/proc/loadavg"):
                try:
                    with open("/proc/loadavg", "r") as f:
                        load_1m = float(f.read().split()[0])
                except (IOError, ValueError) as e:
                    logger.warning(f"Failed to read load average: {e}")

            # Timestamp (Local time as ISO string for frontend)
            current_time = time.strftime("%H:%M:%S")

            stats_history.append({
                "time": current_time,
                "temp": round(cpu_temp, 1),
                "load": round(load_1m, 2)
            })

        except Exception as e:
            logger.error(f"Stats monitor error: {e}", exc_info=True)

        await asyncio.sleep(STATS_COLLECT_INTERVAL_SEC)

async def monitor_interrupts():
    """Background task to poll for GPIO edge events."""
    logger.info("Interrupt monitor: Task started")
    while True:
        try:
            for (chip_path, line_offset), req in line_requests.items():
                # We only check for events on lines that were requested with edge detection
                # In this app, we requested ALL inputs with edge detection
                try:
                    # Use a very short timeout to avoid blocking the event loop
                    if req.wait_edge_events(timeout=INTERRUPT_EDGE_TIMEOUT_SEC):
                        events = req.read_edge_events()
                        for event in events:
                            # Use reverse mapping for efficient lookup
                            pin_num = reverse_pin_mapping.get((chip_path, line_offset), "unknown")
                            event_type = "Rising" if event.event_type == Edge.RISING else "Falling"
                            logger.info(f"Interrupt on Pin {pin_num}: {event_type}")

                            # Handle full queue gracefully
                            try:
                                event_queue.put_nowait({
                                    "pin": pin_num,
                                    "event": event_type,
                                    "timestamp": str(event.timestamp_ns)
                                })
                            except asyncio.QueueFull:
                                logger.warning(f"Event queue full, dropping event for Pin {pin_num}")
                except gpiod.RequestReleasedError:
                    # Line was released, skip it
                    logger.debug(f"Line {chip_path}:{line_offset} was released, skipping")
                except OSError as e:
                    # Hardware I/O error on this specific line
                    logger.debug(f"I/O error on {chip_path}:{line_offset}: {e}")
                except Exception as e:
                    # Log unexpected errors for debugging (not all lines support events)
                    logger.debug(f"Edge event check failed for {chip_path}:{line_offset}: {e}")

            await asyncio.sleep(INTERRUPT_POLL_INTERVAL_SEC)
        except asyncio.CancelledError:
            logger.info("Interrupt monitor: Task cancelled")
            break
        except Exception as e:
            logger.error(f"Interrupt monitor error: {e}", exc_info=True)
            await asyncio.sleep(1)

def validate_gpio_config(config: Dict) -> bool:
    """Validate the GPIO configuration for logical errors and duplicates."""
    if "pins" not in config or not isinstance(config["pins"], list):
        raise ValueError("Config must contain a 'pins' list.")
    
    seen_nums = set()
    seen_hardware = set()
    valid_directions = ["input", "output", "disabled"]
    valid_biases = ["none", "pull-up", "pull-down", "disabled"]

    for pin in config["pins"]:
        # Required fields
        for field in ["num", "chip", "line", "direction", "bias"]:
            if field not in pin:
                raise ValueError(f"Pin {pin.get('num', 'unknown')} is missing required field: {field}")

        # Type checks
        if not isinstance(pin["num"], int) or not isinstance(pin["chip"], int) or not isinstance(pin["line"], int):
            raise ValueError(f"Pin {pin['num']} chip/line/num must be integers.")

        # Duplicate checks
        if pin["num"] in seen_nums:
            raise ValueError(f"Duplicate Pin number detected: {pin['num']}")
        seen_nums.add(pin["num"])

        hw_key = (pin["chip"], pin["line"])
        if hw_key in seen_hardware:
            raise ValueError(f"Duplicate hardware mapping detected: Chip {pin['chip']}, Line {pin['line']}")
        seen_hardware.add(hw_key)

        # Value checks
        if pin["direction"].lower() not in valid_directions:
            raise ValueError(f"Invalid direction for Pin {pin['num']}: {pin['direction']}")
        if pin["bias"].lower() not in valid_biases:
            raise ValueError(f"Invalid bias for Pin {pin['num']}: {pin['bias']}")

    return True

def release_gpios():
    """Release all claimed GPIO lines and stop background tasks."""
    logger.info("Releasing all GPIO lines...")
    global interrupt_task
    if interrupt_task:
        interrupt_task.cancel()

    for key, req in line_requests.items():
        try:
            req.release()
            logger.debug(f"Released line {key}")
        except Exception as e:
            logger.error(f"Error releasing line {key}: {e}")

    line_requests.clear()
    pin_mapping.clear()
    reverse_pin_mapping.clear()

def ensure_config_exists():
    """Ensure user config exists, copy from default template if missing."""
    config_path = os.path.join(APP_DIR, CONFIG_FILE)
    default_path = os.path.join(APP_DIR, DEFAULT_CONFIG_FILE)

    if not os.path.exists(config_path):
        if os.path.exists(default_path):
            logger.info(f"User config not found, copying from {DEFAULT_CONFIG_FILE}")
            shutil.copy(default_path, config_path)
        else:
            logger.error(f"Neither {CONFIG_FILE} nor {DEFAULT_CONFIG_FILE} found!")
            return False
    return True


def init_gpios():
    """Initialise GPIOs based on config file."""
    logger.info("Initialising GPIOs...")

    # Ensure config file exists (copy from default if needed)
    if not ensure_config_exists():
        return

    if not os.path.exists(CONFIG_FILE):
        logger.error(f"Error: {CONFIG_FILE} not found.")
        return

    try:
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
    except Exception as e:
        logger.error(f"Error loading config: {e}")
        return

    for pin_cfg in config.get("pins", []):
        pin_num = pin_cfg.get("num")
        direction_str = pin_cfg.get("direction", "output").lower()
        if direction_str == "disabled":
            logger.info(f"Pin {pin_num} is disabled, skipping hardware initialization.")
            continue

        chip_num = pin_cfg.get("chip")
        line_offset = pin_cfg.get("line")
        bias_str = pin_cfg.get("bias", "none").lower()
        chip_path = f"/dev/gpiochip{chip_num}"

        pin_mapping[pin_num] = (chip_path, line_offset)
        reverse_pin_mapping[(chip_path, line_offset)] = pin_num
        
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
            logger.info(f"Successfully requested Pin {pin_num} ({direction_str}) on {chip_path}")
        except Exception as e:
            msg = f"Failed to request Pin {pin_num} ({direction_str}) on {chip_path}: {e}"
            logger.error(msg)

async def monitor_task_health():
    """Background task to monitor and restart failed background tasks."""
    global interrupt_task, stats_task
    logger.info("Task health monitor: Started")

    while True:
        try:
            await asyncio.sleep(TASK_HEALTH_CHECK_INTERVAL_SEC)

            # Check interrupt monitor
            if interrupt_task is None or interrupt_task.done():
                if interrupt_task and interrupt_task.done():
                    exc = interrupt_task.exception() if not interrupt_task.cancelled() else None
                    if exc:
                        logger.error(f"Interrupt monitor crashed with: {exc}")
                    else:
                        logger.warning("Interrupt monitor stopped unexpectedly")
                logger.info("Restarting interrupt monitor...")
                interrupt_task = asyncio.create_task(monitor_interrupts())

            # Check stats monitor
            if stats_task is None or stats_task.done():
                if stats_task and stats_task.done():
                    exc = stats_task.exception() if not stats_task.cancelled() else None
                    if exc:
                        logger.error(f"Stats monitor crashed with: {exc}")
                    else:
                        logger.warning("Stats monitor stopped unexpectedly")
                logger.info("Restarting stats monitor...")
                stats_task = asyncio.create_task(monitor_stats())

        except asyncio.CancelledError:
            logger.info("Task health monitor: Cancelled")
            break
        except Exception as e:
            logger.error(f"Task health monitor error: {e}", exc_info=True)
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global interrupt_task, stats_task, task_monitor_task
    # Startup
    logger.info("Starting LoxIO Core API...")
    init_gpios()
    interrupt_task = asyncio.create_task(monitor_interrupts())
    stats_task = asyncio.create_task(monitor_stats())
    task_monitor_task = asyncio.create_task(monitor_task_health())
    logger.info("All background tasks started")
    yield
    # Shutdown
    logger.info("Shutting down LoxIO Core API...")

    # Cancel all background tasks
    tasks_to_cancel = [interrupt_task, stats_task, task_monitor_task]
    for task in tasks_to_cancel:
        if task:
            task.cancel()

    # Wait for all tasks to complete
    try:
        await asyncio.gather(*[t for t in tasks_to_cancel if t], return_exceptions=True)
    except asyncio.CancelledError:
        pass

    # Release GPIO resources
    for key, req in line_requests.items():
        try:
            req.release()
            logger.debug(f"Released line {key}")
        except Exception as e:
            logger.warning(f"Error releasing line {key}: {e}")

    line_requests.clear()
    pin_mapping.clear()
    reverse_pin_mapping.clear()
    logger.info("Cleanup complete")

app = FastAPI(
    title="LoxIO Core API",
    description="""
**LoxIO Core** is a high-performance GPIO control system by **RS Soft**.
Designed for Orange Pi Zero 3.

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

app.mount("/static", StaticFiles(directory=APP_DIR), name="static")

@app.get("/")
async def root():
    return {
        "message": "LoxIO Core API with Input/Interrupt Support",
        "active_pins": list(pin_mapping.keys())
    }

def get_system_info():
    info = {
        "board": "unknown",
        "os": "unknown",
        "hostname": socket.gethostname(),
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
        except (IOError, IndexError) as e:
            logger.debug(f"Could not read board info: {e}")

    # OS Info
    if os.path.exists("/etc/os-release"):
        try:
            with open("/etc/os-release", "r") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        info["os"] = line.split("=")[1].strip().strip('"')
                        break
        except (IOError, IndexError) as e:
            logger.debug(f"Could not read OS info: {e}")

    # Uptime
    if os.path.exists("/proc/uptime"):
        try:
            with open("/proc/uptime", "r") as f:
                uptime_seconds = float(f.readline().split()[0])
                info["uptime"] = str(timedelta(seconds=int(uptime_seconds)))
        except (IOError, ValueError, IndexError) as e:
            logger.debug(f"Could not read uptime: {e}")

    # Load Average
    if os.path.exists("/proc/loadavg"):
        try:
            with open("/proc/loadavg", "r") as f:
                info["load_avg"] = [float(x) for x in f.read().split()[:3]]
        except (IOError, ValueError) as e:
            logger.debug(f"Could not read load average: {e}")

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
        except (IOError, ValueError, ZeroDivisionError) as e:
            logger.debug(f"Could not read memory info: {e}")

    return info

@app.get("/health")
async def health():
    # CPU temperature
    cpu_temp = "unknown"
    try:
        if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                cpu_temp = int(f.read().strip()) / 1000.0
    except (IOError, ValueError) as e:
        logger.debug(f"Could not read CPU temperature: {e}")

    sys_info = get_system_info()

    return {
        "status": "healthy",
        "board_info": {
            "name": sys_info["board"],
            "hostname": sys_info["hostname"],
            "os": sys_info["os"],
            "kernel": sys_info["kernel"],
            "arch": sys_info["arch"],
            "uptime": sys_info["uptime"]
        },
        "system_stats": {
            "cpu_temp_c": cpu_temp,
            "load_avg": sys_info["load_avg"],
            "ram": sys_info["ram"]
        },
        "gpio_status": {
            "initialized": len(line_requests) > 0,
            "claimed_pins_count": len(line_requests),
            "interrupt_monitor_running": interrupt_task is not None and not interrupt_task.done()
        }
    }

@app.get("/stats/history")
async def get_stats_history():
    """Return the collected system stats history."""
    return {"history": list(stats_history)}

@app.get("/pins/status")
async def get_status():
    status = []
    if not os.path.exists(CONFIG_FILE):
        raise HTTPException(status_code=404, detail="Config missing")
    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)
    for pin_cfg in config["pins"]:
        if pin_cfg.get("direction") == "disabled":
            continue
            
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
                logger.debug(f"Could not read value for pin {pin_num}: {e}")
        
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
    client_ip = request.client.host if request.client else "unknown"
    try:
        # Loxone sends Content-Type: text/plain, which FastAPI rejects for Pydantic models.
        # We manually parse the body to support this behavior.
        body_bytes = await request.body()
        if not body_bytes:
            logger.warning(f"Empty request body from {client_ip} on /pins/set")
            raise HTTPException(status_code=400, detail="Empty body")

        try:
            body_json = json.loads(body_bytes)
            data = PinState(**body_json)
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON from {client_ip} on /pins/set: {e}")
            raise HTTPException(status_code=422, detail="Invalid JSON format")
        except Exception as e:
            logger.warning(f"Validation error from {client_ip} on /pins/set: {e}")
            raise HTTPException(status_code=422, detail=f"Validation error: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error parsing request from {client_ip}: {e}")
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
        return {"logs": ["Log file not found"]}
    try:
        # Read the last N lines efficiently
        with open(LOG_FILE, "r") as f:
            # For simplicity with rotating logs, we just read all lines if file is small, 
            # or use deque for large files.
            content = deque(f, maxlen=lines)
            return {"logs": [line.strip() for line in content]}
    except Exception as e:
        logger.error(f"Error reading logs: {e}")
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
            except Exception as e:
                logger.debug(f"Could not read value for pin {pin_num} in loxone/status: {e}")
        
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
    except (IOError, ValueError) as e:
        logger.debug(f"Could not read CPU temperature for loxone/stats: {e}")

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
        except (IOError, ValueError, IndexError) as e:
            logger.debug(f"Could not read uptime for loxone/stats: {e}")
    output.append(f"UptimeHours={uptime_hours}")

    return "\n".join(output)

def get_ip_address():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except (OSError, socket.error) as e:
        logger.debug(f"Could not determine IP address: {e}")
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
        f'<VirtualInHttp Title="LoxIO_Core_Inputs ({ip_addr})" Comment="RS Soft LoxIO Core" Address="{base_url}/loxone/status" HintText="" PollingTime="1">',
        '  <Info templateType="2" minVersion="15050304"/>'
    ]

    for pin_cfg in config["pins"]:
        # Only generate Virtual Inputs for pins explicitly configured as 'input'
        if pin_cfg.get("direction", "output").lower() == "input":
            pin_num = pin_cfg["num"]
            name = pin_cfg.get("name", str(pin_num))
            
            # Use Analog="false" for standard digital inputs to appear correctly in Loxone
            xml_lines.append(
                f'  <VirtualInHttpCmd Title="Pin {pin_num} ({name})" Comment="" Check="Pin {pin_num}=\\v" '
                f'Signed="true" Analog="false" SourceValLow="0" DestValLow="0" SourceValHigh="1" DestValHigh="1" '
                f'DefVal="0" MinVal="0" MaxVal="1" Unit="" HintText=""/>'
            )

    xml_lines.append('</VirtualInHttp>')
    # Adding UTF-8 BOM (\ufeff) for Loxone Config compatibility on Windows
    xml_content = "\ufeff" + "\n".join(xml_lines)
    
    return Response(content=xml_content, media_type="application/xml", headers={"Content-Disposition": 'attachment; filename="LoxIO_Inputs.xml"'})

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

    xml_lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<VirtualOut Title="LoxIO_Core_Outputs ({ip_addr})" Comment="RS Soft LoxIO Core" Address="{base_url}" HintText=""'
        ' CloseAfterSend="true" CmdSep=";">',
        '  <Info templateType="3" minVersion="16021217"/>'
    ]

    for pin_cfg in config["pins"]:
        if pin_cfg.get("direction", "output").lower() == "output":
            pin_num = pin_cfg["num"]
            name = pin_cfg.get("name", str(pin_num))
            
            on_body = json.dumps({"pin_num": pin_num, "state": 1}).replace('"', '&quot;')
            off_body = json.dumps({"pin_num": pin_num, "state": 0}).replace('"', '&quot;')
            
            xml_lines.append(
                f'  <VirtualOutCmd Title="Pin {pin_num} ({name})" Comment="" '
                f'CmdOnMethod="POST" CmdOffMethod="POST" '
                f'CmdOn="/pins/set" CmdOnHTTP="" CmdOnPost="{on_body}" '
                f'CmdOff="/pins/set" CmdOffHTTP="" CmdOffPost="{off_body}" '
                f'CmdAnswer="" Analog="false" Repeat="0" RepeatRate="0" HintText=""/>'
            )

    xml_lines.append('</VirtualOut>')
    
    # Adding UTF-8 BOM (\ufeff)
    xml_content = "\ufeff" + "\n".join(xml_lines)
    
    return Response(content=xml_content, media_type="application/xml", headers={"Content-Disposition": 'attachment; filename="LoxIO_Outputs.xml"'})

@app.get("/loxone/template/stats", response_class=Response)
async def get_loxone_stats_template():
    """Generate Loxone Virtual Input Template XML for System Stats"""
    ip_addr = get_ip_address()
    base_url = f"http://{ip_addr}:8000"
    
    xml_lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<VirtualInHttp Title="LoxIO_Core_Stats ({ip_addr})" Comment="RS Soft LoxIO Core" Address="{base_url}/loxone/stats" HintText="" PollingTime="60">',
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

    # Adding UTF-8 BOM (\ufeff)
    xml_content = "\ufeff" + "\n".join(xml_lines)
    
    return Response(content=xml_content, media_type="application/xml", headers={"Content-Disposition": 'attachment; filename="LoxIO_Stats.xml"'})

@app.get("/update/check")
async def check_update():
    """Check if update is available"""
    try:
        # Fetch latest changes without applying
        subprocess.run(["git", "fetch", "origin", "main"], cwd=APP_DIR, check=True)
        
        # Get local and remote hashes
        local_hash = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=APP_DIR, text=True).strip()
        remote_hash = subprocess.check_output(["git", "rev-parse", "origin/main"], cwd=APP_DIR, text=True).strip()
        
        update_available = local_hash != remote_hash
        return {
            "update_available": update_available,
            "local_hash": local_hash,
            "remote_hash": remote_hash
        }
    except Exception as e:
        logger.warning(f"Update check failed: {e}")
        local_hash = "Unknown"
        try:
            local_hash = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=APP_DIR, text=True).strip()
        except subprocess.CalledProcessError as git_err:
            logger.debug(f"Could not get local git hash: {git_err}")
        return {
            "error": str(e),
            "update_available": False,
            "local_hash": local_hash
        }

@app.post("/update/ota")
async def ota_update(force: bool = False):
    """Trigger OTA update via safe shell script"""
    try:
        # First check if update is needed (unless forced)
        if not force:
             subprocess.run(["git", "fetch", "origin", "main"], cwd=APP_DIR, check=True)
             local = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=APP_DIR, text=True).strip()
             remote = subprocess.check_output(["git", "rev-parse", "origin/main"], cwd=APP_DIR, text=True).strip()
             if local == remote:
                 return {"status": "skipped", "message": "Already up to date"}

        # Verification check: Ensure script exists
        update_script = os.path.join(SCRIPTS_DIR, "update_safe.sh")
        if not os.path.exists(update_script):
             return {"status": "error", "message": f"Safety script missing at {update_script}. Cannot update safely."}

        # Run the safe update script in non-blocking way (fire and forget)
        # We use start_new_session to detach prompts/signals so it survives restart
        subprocess.Popen(
            ["/bin/bash", update_script],
            cwd=APP_DIR,
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
                # Use startswith to avoid matching "disconnected"
                is_connected = state.lower().startswith("connected")
                
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

@app.post("/system/reboot")
async def system_reboot():
    """Reboot the Orange Pi"""
    try:
        # Use Popen to let the response send before reboot
        subprocess.Popen(["reboot"])
        return {"status": "success", "message": "Reboot sequence initiated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/update/zip")
async def zip_update(file: UploadFile = File(...)):
    """Handle manual ZIP update upload"""
    try:
        tmp_path = f"/tmp/{file.filename}"
        with open(tmp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Trigger the manual update script in the background
        # (It will restart this service)
        manual_update_script = os.path.join(SCRIPTS_DIR, "update_manual.sh")
        subprocess.Popen(["/bin/bash", manual_update_script, tmp_path])
        
        return {"status": "success", "message": "ZIP upload received, update started"}
    except Exception as e:
        return {"error": str(e)}

@app.post("/system/shutdown")
async def system_shutdown():
    """Shutdown the Orange Pi"""
    try:
        # Use Popen to let the response send before shutdown
        subprocess.Popen(["shutdown", "-h", "now"])
        return {"status": "success", "message": "Shutdown sequence initiated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
@app.get("/config")
async def get_config():
    """Retrieve the current GPIO configuration."""
    if not os.path.exists(CONFIG_FILE):
         raise HTTPException(status_code=404, detail="Config file missing")
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/config/update")
async def update_config(config: Dict):
    """Update the GPIO configuration and reload hardware."""
    try:
        # Validate configuration before applying
        try:
            validate_gpio_config(config)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        
        # Save to file
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        
        logger.info("Configuration updated via API. Reloading hardware...")
        
        # Hot-reload GPIOs
        release_gpios()
        init_gpios()
        
        # Restart interrupt task
        global interrupt_task
        interrupt_task = asyncio.create_task(monitor_interrupts())
        
        return {"status": "success", "message": "Configuration updated and hardware reloaded"}
    except Exception as e:
        logger.error(f"Config update failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
