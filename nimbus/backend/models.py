"""
Nimbus — shared data models and island resource definitions.

These are prototype/simulation models only. The physics and environment are
simulated; nothing here controls a real power grid.
"""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class ResourceDef:
    """Static definition of an island resource."""

    id: str
    name: str
    criticality: int                     # 0..100, higher = more critical
    continuously_throttleable: bool      # can be varied smoothly (e.g. desal)
    shed_capable: bool                   # can be disconnected entirely
    min_operating_pct: float             # safe minimum operating level (%)
    base_demand_kw: float                # power wanted at 100% operation
    description: str


RESOURCE_DEFS: List[ResourceDef] = [
    ResourceDef(
        id="hospital",
        name="Hospital",
        criticality=100,
        continuously_throttleable=False,
        shed_capable=False,
        min_operating_pct=100.0,
        base_demand_kw=40.0,
        description="Critical services — never shed.",
    ),
    ResourceDef(
        id="desalination",
        name="Desalination",
        criticality=90,
        continuously_throttleable=True,
        shed_capable=False,
        min_operating_pct=30.0,
        base_demand_kw=75.0,
        description="Water plant — continuously throttleable.",
    ),
    ResourceDef(
        id="residential",
        name="Residential",
        criticality=70,
        continuously_throttleable=True,
        shed_capable=True,
        min_operating_pct=40.0,
        base_demand_kw=100.0,
        description="Homes — partially flexible.",
    ),
    ResourceDef(
        id="resort",
        name="Resort",
        criticality=20,
        continuously_throttleable=True,
        shed_capable=True,
        min_operating_pct=50.0,
        base_demand_kw=50.0,
        description="Tourism — lowest priority.",
    ),
]


def resource_def(res_id: str) -> ResourceDef:
    for r in RESOURCE_DEFS:
        if r.id == res_id:
            return r
    raise KeyError(f"Unknown resource: {res_id}")


# ---------------------------------------------------------------------------
# Tunable Nimbus parameters. These are prototype tuning constants, not claimed
# to be scientifically optimal. Kept configurable for the demo/evaluation.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class NimbusConfig:
    # energy-balance trajectory
    ema_tau_s: float = 0.4                 # smoothing time constant for net power
    target_net_kw: float = 5.0            # desired small surplus (keeps battery topped)
    lead_s: float = 1.0                   # look-ahead used for early detection (velocity)
    accel_lead_s: float = 0.6             # extra look-ahead weighting for acceleration

    # PD controller for desalination (last-resort throttle)
    kp: float = 0.20
    kd: float = 0.08
    desal_min_pct: float = 30.0            # physical lower bound for the plant
    desal_deadzone_kw: float = 18.0        # do not throttle water for mild deficits

    # residential reduction thresholds
    residential_reduce_net_kw: float = -55.0   # filtered net below this -> reduce homes
    residential_recover_net_kw: float = -15.0  # hysteresis: back above this -> restore

    # resort shed thresholds (battery + projected trajectory)
    battery_critical_pct: float = 24.0
    projected_shed_net_kw: float = -160.0
    resort_recover_net_kw: float = 8.0
    cooldown_s: float = 4.0
    restore_battery_pct: float = 26.0

    # battery hard limits
    battery_capacity_kwh: float = 900.0
    battery_init_pct: float = 75.0
    max_charge_rate_kw: float = 250.0
    max_discharge_rate_kw: float = 250.0

    # generation
    solar_max_kw: float = 280.0
    wind_max_kw: float = 170.0

    dt_s: float = 0.1


DEFAULT_CONFIG = NimbusConfig()
