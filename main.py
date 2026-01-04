import json
import gpiod
import os
import asyncio
from datetime import timedelta
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from gpiod.line import Direction, Value, Edge, Bias
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
async def set_pin(data: PinState):
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
