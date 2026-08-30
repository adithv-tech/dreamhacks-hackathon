"""
Nimbus — FastAPI backend.

Serves the static frontend, runs the island simulation + controller in-memory,
streams telemetry over a WebSocket, handles event injection / resets, and
exposes the automated evaluation endpoint.

Prototype/simulation software only. Not a real grid controller.
"""

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from models import DEFAULT_CONFIG, NimbusConfig, RESOURCE_DEFS
from simulation import IslandSim
from controller import NaiveController, ReactiveController, NimbusController
from explainability import ExplainabilityEngine
from events import EVENT_CODES, EVENT_META, create_event
from evaluation import Evaluator

app = FastAPI(title="Nimbus")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# ---------------------------------------------------------------------------
# Controller registry
# ---------------------------------------------------------------------------
CONTROLLERS = {
    "naive": NaiveController,
    "reactive": ReactiveController,
    "nimbus": NimbusController,
}

STATE = {
    "sim": IslandSim(DEFAULT_CONFIG),
    "expl": ExplainabilityEngine(),
    "controller": None,
    "controller_name": "nimbus",
    "running": True,
    "history": [],           # recent telemetry snapshots for the live chart
    "history_max": 300,
    "start_time": time.time(),
}


def current_controller():
    if STATE["controller"] is None:
        bind_controller("nimbus")
    return STATE["controller"]


def bind_controller(name: str):
    STATE["controller_name"] = name
    STATE["controller"] = CONTROLLERS[name](DEFAULT_CONFIG)
    STATE["controller"].bind(STATE["sim"], STATE["expl"])


def reset_state(battery_pct: Optional[float] = None):
    STATE["sim"].reset(battery_pct=battery_pct)
    STATE["expl"].reset()
    STATE["history"].clear()
    bind_controller(STATE["controller_name"])
    STATE["start_time"] = time.time()


# ---------------------------------------------------------------------------
# Simulation loop
# ---------------------------------------------------------------------------
async def simulation_loop():
    while True:
        if STATE["running"]:
            ctrl = current_controller()
            snap = STATE["sim"].snapshot()
            ctrl.set_time(snap["time_s"])
            ctrl.decide(snap)
            snap = STATE["sim"].step()
            snap["controller"] = ctrl.name
            STATE["history"].append(snap)
            if len(STATE["history"]) > STATE["history_max"]:
                STATE["history"] = STATE["history"][-STATE["history_max"]:]
            await asyncio.sleep(STATE["sim"].dt)
        else:
            await asyncio.sleep(0.1)


@app.on_event("startup")
async def on_startup():
    reset_state()
    asyncio.create_task(simulation_loop())


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------
class EventRequest(BaseModel):
    code: str
    severity: Optional[float] = 1.0


class ControllerRequest(BaseModel):
    controller: str


class ResetRequest(BaseModel):
    battery_pct: Optional[float] = None
    controller: Optional[str] = None


class SliderRequest(BaseModel):
    solar: Optional[float] = None
    wind: Optional[float] = None
    battery_pct: Optional[float] = None
    residential: Optional[float] = None
    desalination: Optional[float] = None
    resort: Optional[float] = None


@app.get("/")
async def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/api/meta")
async def meta():
    return {
        "controllers": list(CONTROLLERS.keys()),
        "events": EVENT_META,
        "resources": [r.__dict__ for r in RESOURCE_DEFS],
        "config": DEFAULT_CONFIG.__dict__,
    }


@app.get("/api/snapshot")
async def snapshot():
    s = STATE["sim"].snapshot()
    s["controller"] = STATE["controller_name"]
    return s


@app.post("/api/event")
async def inject_event(req: EventRequest):
    if req.code not in EVENT_CODES:
        return {"ok": False, "error": f"unknown event {req.code}"}
    # Clear any manual slider overrides first so the disturbance's effect on
    # generation/demand is always visible rather than masked by the overrides.
    for k in STATE["sim"].manual:
        STATE["sim"].manual[k] = None
    STATE["sim"].inject_event(create_event(req.code, severity=req.severity))
    return {"ok": True, "event": req.code}


@app.post("/api/controller")
async def set_controller(req: ControllerRequest):
    if req.controller not in CONTROLLERS:
        return {"ok": False, "error": f"unknown controller {req.controller}"}
    # reset simulation for a fair comparison, keep battery where it is
    STATE["sim"].reset(battery_pct=STATE["sim"].battery_pct)
    STATE["expl"].reset()
    STATE["history"].clear()
    bind_controller(req.controller)
    return {"ok": True, "controller": req.controller}


@app.post("/api/reset")
async def reset_system(req: ResetRequest):
    if req.controller and req.controller not in CONTROLLERS:
        return {"ok": False, "error": f"unknown controller {req.controller}"}
    if req.controller:
        STATE["controller_name"] = req.controller
    reset_state(battery_pct=req.battery_pct)
    return {"ok": True, "controller": STATE["controller_name"]}


@app.post("/api/sliders")
async def set_sliders(req: SliderRequest):
    STATE["sim"].manual["solar"] = req.solar
    STATE["sim"].manual["wind"] = req.wind
    STATE["sim"].manual["battery_pct"] = req.battery_pct
    STATE["sim"].manual["residential"] = req.residential
    STATE["sim"].manual["desalination"] = req.desalination
    STATE["sim"].manual["resort"] = req.resort
    return {"ok": True}


@app.post("/api/sliders/clear")
async def clear_sliders():
    for k in STATE["sim"].manual:
        STATE["sim"].manual[k] = None
    return {"ok": True}


@app.get("/api/explain")
async def explain():
    return STATE["expl"].latest


@app.get("/api/history")
async def history(limit: int = 200):
    return STATE["history"][-limit:]


@app.get("/api/evaluate")
async def evaluate(n: int = 120, seed: int = 7):
    n = max(1, min(500, n))
    evaluator = Evaluator(n_scenarios=n, seed=seed)
    aggregates = evaluator.run()
    # include per-controller sample rows for charts
    return {
        "aggregates": aggregates,
        "rows": evaluator.summary_rows(),
        "n_scenarios": n,
        "seed": seed,
    }


# ---------------------------------------------------------------------------
# WebSocket telemetry
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    client_id = str(uuid.uuid4())
    try:
        while True:
            # client may send control messages over the same socket
            data = await asyncio.wait_for(ws.receive_text(), timeout=0.05)
            _handle_ws_message(data)
    except asyncio.TimeoutError:
        pass
    except WebSocketDisconnect:
        return
    # stream loop
    try:
        while True:
            snap = STATE["sim"].snapshot()
            snap["controller"] = STATE["controller_name"]
            payload = {
                "telemetry": snap,
                "decision": STATE["expl"].latest,
                "history": STATE["history"][-120:],
            }
            await ws.send_text(json.dumps(payload))
            await asyncio.sleep(STATE["sim"].dt)
    except WebSocketDisconnect:
        pass


def _handle_ws_message(msg: str):
    try:
        req = json.loads(msg)
    except Exception:
        return
    if req.get("type") == "event":
        asyncio.create_task(inject_event(EventRequest(**req.get("payload", {}))))
    elif req.get("type") == "controller":
        asyncio.create_task(set_controller(ControllerRequest(**req.get("payload", {}))))
    elif req.get("type") == "reset":
        asyncio.create_task(reset_system(ResetRequest(**req.get("payload", {}))))
    elif req.get("type") == "sliders":
        asyncio.create_task(set_sliders(SliderRequest(**req.get("payload", {}))))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
