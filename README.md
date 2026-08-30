# dreamhacks-hackathon — Nimbus

**Nimbus: Autonomous Island Energy & Resource Orchestrator**

A real-time autonomous energy-management system for an isolated island
microgrid. Nimbus observes live environmental and energy data, detects
deteriorating grid conditions from the trajectory of the energy balance before
they become severe, and autonomously allocates limited energy among competing
island resources — protecting critical services, smoothly throttling flexible
infrastructure, minimizing unnecessary load shedding, and restoring services
when conditions recover.

> ⚠️ **Prototype.** The island physics and environment are **simulated**.
> This is not real grid-control hardware and does not claim to invent new
> control-theory algorithms. See `nimbus/README.md` for the full detail.

## Quick start

```bash
cd nimbus
./run.sh                     # creates .venv, installs deps, starts on :8000
# open http://localhost:8000
```

Or manually:

```bash
cd nimbus
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python backend/main.py
```

## What's inside

- **`nimbus/backend/`** — FastAPI + WebSocket backend (simulation, controllers,
  events, hysteresis, evaluation, explainability).
- **`nimbus/frontend/`** — polished live dashboard (Chart.js): gauges,
  energy-balance trajectory, resource control panel, controller comparison,
  event replay, "why?" explanations, and 120-scenario quantitative evaluation.

## Feature checklist

- Solar, wind, battery, and island demand are simulated continuously.
- Six disturbances: Storm, Cloud Cover, Wind Drop, Tourist Surge,
  Water Emergency, Compound Crisis.
- Four resources with distinct priorities (Hospital > Desalination >
  Residential > Resort).
- Nimbus uses filtered energy-balance **velocity** and **acceleration** as
  early-warning signals.
- Desalination is continuously throttleable via a PD controller (last resort).
- Flexible loads shed / restore via a `NORMAL → SHED → COOLDOWN → NORMAL`
  state machine with hysteresis.
- Every major action has a human-readable explanation.
- Naive, Reactive, and Nimbus controllers can be compared on the **same**
  disturbance (fair comparison; switching controllers resets state).
- 120+ randomized scenarios evaluated automatically; metrics computed directly
  from simulation.
- Live dashboard + `/reset` recovery + 5-minute continuous-operation test.
