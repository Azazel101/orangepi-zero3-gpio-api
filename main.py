import json
import gpiod
import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from gpiod.line import Direction, Value
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

def init_gpios():
    print("Initialising GPIOs (v2 API - Robust Mode)...")
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
        line_num = pin_cfg.get("line")
        chip_path = f"/dev/gpiochip{chip_num}"
        
        pin_mapping[pin_num] = (chip_path, line_num)
        
        try:
            # Request individual line to avoid [Errno 517] Busy
            req = gpiod.request_lines(
                chip_path,
                consumer=f"fastapi-pin-{pin_num}",
                config={line_num: gpiod.LineSettings(
                    direction=Direction.OUTPUT,
                    output_value=Value.INACTIVE
                )}
            )
            line_requests[(chip_path, line_num)] = req
            print(f"Successfully requested Pin {pin_num} (Chip {chip_num}, Line {line_num})")
        except Exception as e:
            print(f"Failed to request Pin {pin_num} (Line {line_num}) on {chip_path}: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    init_gpios()
    yield
    # Shutdown
    print("Cleaning up GPIOs...")
    for req in line_requests.values():
        try:
            req.release()
        except:
            pass
    line_requests.clear()

app = FastAPI(
    title="Orange Pi Zero 3 GPIO API (v2)",
    description="""
A FastAPI application to control GPIO pins via libgpiod v2.x.

![Orange Pi Zero 3 Pinout](/static/pinout.png)

### Corrected Mapping Table (Orange Pi Zero 3 v1.2)
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
    version="1.1.0",
    lifespan=lifespan
)

app.mount("/static", StaticFiles(directory="/root/opi_gpio_app"), name="static")

@app.get("/logs")
async def get_logs(lines: int = 100):
    if not os.path.exists(LOG_FILE):
        return {"message": "Log file not found"}
    try:
        with open(LOG_FILE, "r") as f:
            # Get last N lines
            content = f.readlines()
            return {"logs": content[-lines:]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
async def root():
    return {
        "message": "Orange Pi GPIO API is running",
        "docs": "/docs",
        "active_pins": list(pin_mapping.keys())
    }

@app.get("/pins")
async def get_pins():
    if not os.path.exists(CONFIG_FILE):
        raise HTTPException(status_code=404, detail="Config file not found")
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

@app.get("/pins/status")
async def get_status():
    status = []
    config = await get_pins()
    for pin_cfg in config["pins"]:
        pin_num = pin_cfg["num"]
        mapping = pin_mapping.get(pin_num)
        current_val = -1
        is_active = False
        
        if mapping and mapping in line_requests:
            is_active = True
            try:
                # get_value returns Value object, we convert to int
                val = line_requests[mapping].get_value(mapping[1])
                current_val = 1 if val == Value.ACTIVE else 0
            except Exception as e:
                print(f"Error reading Pin {pin_num}: {e}")
        
        status.append({
            **pin_cfg,
            "active": is_active,
            "current_state": current_val
        })
    return status

@app.post("/pins/toggle/{pin_num}")
async def toggle_pin(pin_num: int):
    if pin_num not in pin_mapping:
        raise HTTPException(status_code=404, detail=f"Pin {pin_num} not configured.")
    
    mapping = pin_mapping[pin_num]
    if mapping not in line_requests:
        raise HTTPException(status_code=500, detail=f"Pin {pin_num} not requested/active.")
    
    try:
        current = line_requests[mapping].get_value(mapping[1])
        new_state = Value.INACTIVE if current == Value.ACTIVE else Value.ACTIVE
        line_requests[mapping].set_value(mapping[1], new_state)
        return {
            "pin_num": pin_num, 
            "state": 1 if new_state == Value.ACTIVE else 0, 
            "status": "toggled"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/pins/set")
async def set_pin(data: PinState):
    if data.pin_num not in pin_mapping:
        raise HTTPException(status_code=404, detail=f"Pin {data.pin_num} not configured.")
    
    mapping = pin_mapping[data.pin_num]
    if mapping not in line_requests:
        raise HTTPException(status_code=500, detail=f"Line {mapping} not active.")

    req = line_requests[mapping]
    line_offset = mapping[1]
    val = Value.ACTIVE if data.state == 1 else Value.INACTIVE
    try:
        req.set_value(line_offset, val)
        return {"pin_num": data.pin_num, "state": data.state, "status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to set pin: {str(e)}")

@app.post("/pins/all/low")
async def set_all_low():
    results = []
    for pin_num, mapping in pin_mapping.items():
        if mapping in line_requests:
            try:
                line_requests[mapping].set_value(mapping[1], Value.INACTIVE)
                results.append(pin_num)
            except:
                pass
    return {"status": "success", "set_low": results}

@app.post("/pins/all/high")
async def set_all_high():
    results = []
    for pin_num, mapping in pin_mapping.items():
        if mapping in line_requests:
            try:
                line_requests[mapping].set_value(mapping[1], Value.ACTIVE)
                results.append(pin_num)
            except:
                pass
    return {"status": "success", "set_high": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
