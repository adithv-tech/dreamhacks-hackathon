# Nimbus — Autonomous Island Energy & Resource Orchestrator

Nimbus is a **prototype** real-time autonomous energy-management system for an
isolated island microgrid. It continuously observes live environmental and
energy data, **detects deteriorating grid conditions before they become
severe**, and automatically decides how to distribute limited energy among
competing island resources.

> ⚠️ **This is a simulation and software prototype.** The island physics and
> environment are simulated in-memory. This is **not** real-world grid control
> hardware, and Nimbus does **not** invent a new control-theory algorithm. Its
> contribution is an *integrated* decision architecture — real-time sensing +
> early trajectory detection + priority-aware allocation + continuous
> flexible-load control + discrete safety decisions + quantitative evaluation —
> not PD control itself.

---

## The story

When an isolated island suddenly loses renewable generation or faces a demand
surge, Nimbus:

1. **Notices the problem is getting worse** (short-term early detection from
   the trajectory of the live energy balance, *not* weather forecasting).
2. **Determines what must be protected** (hospital > water > homes > resort).
3. **Intelligently reduces flexible consumption** — sheds the low-priority
   resort first, smoothly throttles the desalination plant, then residential.
4. **Keeps critical services running**.
5. **Restores resources in an orderly manner** when conditions recover.
6. **Quantitatively demonstrates** whether the strategy actually performs
   better against two baselines.

## Run it

```bash
cd nimbus
python3 -m venv .venv
source .venv/bin/activate
pip install fastapi "uvicorn[standard]" websockets

python backend/main.py          # or: uvicorn backend.main:app --port 8000
# open http://localhost:8000/static/index.html  (or just http://localhost:8000/)
```

The API docs are at `http://localhost:8000/docs`.

## Architecture

```
nimbus/
├── backend/
│   ├── main.py              FastAPI app, WebSocket telemetry, REST endpoints
│   ├── simulation.py        island microgrid physics (solar, wind, battery, demand)
│   ├── controller.py        Naive, Reactive, and Nimbus controllers
│   ├── resource_manager.py  hospital / desal / residential / resort state
│   ├── events.py            STORM, CLOUD COVER, WIND DROP, TOURIST SURGE, …
│   ├── hysteresis.py        anti-flapping two-threshold bands + shed state machine
│   ├── evaluation.py        120+ randomized scenarios, per-controller metrics
│   ├── explainability.py    human-readable "why?" reasons for every action
│   └── models.py            shared resource definitions and tunable config
├── frontend/
│   ├── index.html           dashboard
│   ├── app.js               live UI, event injection, comparison, evaluation
│   └── styles.css           styling
└── README.md
```

* FastAPI backend, single process, fully in-memory. No database/auth/cloud.
* Live telemetry over WebSockets (~100 ms tick).
* Chart.js for visualization.

## The three controllers

| Controller | Reacts to | Early detection | Desal control | Shedding |
|---|---|---|---|---|
| **Naive** | battery % only | ✗ | none (100%) | abrupt, on fixed thresholds |
| **Reactive** | battery % + current net power + hysteresis | ✗ | throttles on current deficit | on battery depletion |
| **Nimbus** | filtered balance + velocity + acceleration | ✓ | PD (last resort) | priority-aware, with cooldown |

## Key ideas

- **Early detection** uses the *trajectory* of the smoothed energy balance:
  `velocity = d(net)/dt` and `acceleration = d(velocity)/dt`. A battery reading
  alone can look safe while `net=-20 kW, vel=-30 kW/s, accel=-12 kW/s²` reveals
  rapid deterioration.
- **PD control** smoothly throttles the desalination plant (`100% → 87% → 73%`),
  never `100% → OFF`. The derivative term is on the *same* error it corrects
  (`target_net − filtered_net`), not a grid-wide velocity fed into an unrelated
  term.
- **Shed state machine** `NORMAL → SHED → COOLDOWN → NORMAL` with a cooldown
  period prevents rapid shed/restore flapping during noisy conditions.
- **Explainability**: every major action logs a human-readable reason.
- **Evaluation**: 120+ randomized disturbances are replayed under all three
  controllers with identical initial conditions; metrics (critical uptime,
  water availability, load shed, recovery time, oscillation, min battery) come
  directly from the simulation. The overall **Nimbus Score** is a clearly
  labelled *prototype* evaluation metric with configurable weighting.

## Fair comparison

The same disturbance can be injected and replayed under Naive, Reactive, or
Nimbus. Switching controllers resets the island to the same starting state so
the comparison is apples-to-apples.
