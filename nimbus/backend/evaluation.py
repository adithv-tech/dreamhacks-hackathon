"""
Nimbus — automated evaluation.

Runs N randomized disturbances through every controller (Naive, Reactive,
Nimbus) under identical conditions and computes quantitative performance
metrics from the actual simulation. Results power the comparison table/charts.

This is a prototype evaluation metric, not a claim of scientific optimality.
"""

import math
import random
from typing import Dict, List

from models import DEFAULT_CONFIG, NimbusConfig
from simulation import IslandSim
from controller import NaiveController, ReactiveController, NimbusController
from explainability import ExplainabilityEngine
from events import EVENT_CODES, create_event


class Scenario:
    """Randomly generated disturbance, shared identically across controllers."""

    def __init__(self, seed: int, rng: random.Random):
        self.seed = seed
        self.event_code = rng.choice(EVENT_CODES)
        self.severity = round(rng.uniform(0.5, 0.9), 2)
        self.init_battery_pct = round(rng.uniform(45, 95), 1)
        self.solar_scale = round(rng.uniform(0.6, 1.3), 2)
        self.wind_scale = round(rng.uniform(0.6, 1.3), 2)
        self.demand_scale = round(rng.uniform(0.8, 1.3), 2)
        # randomness that makes each scenario a little different
        self.event_seed = seed


class ScenarioResult:
    def __init__(self, controller: str):
        self.controller = controller
        self.critical_uptime = 0.0
        self.water_availability = 0.0
        self.total_load_shed_kwh = 0.0
        self.shed_events = 0
        self.recovery_time_s = float("inf")
        self.min_battery_pct = 100.0
        self.instability = 0.0
        self.score = 0.0
        self.served_ticks = 0
        self.total_ticks = 0


def run_scenario(sim: IslandSim, controller, sc: Scenario,
                 expl: ExplainabilityEngine) -> ScenarioResult:
    res = ScenarioResult(controller.name)
    cfg = sim.cfg

    # reset sim and controller
    sim.reset(battery_pct=sc.init_battery_pct)
    controller.bind(sim, expl)

    # warmup without event
    warmup_s = 3.0
    for _ in range(int(warmup_s / sim.dt)):
        snap = sim.snapshot()
        controller.set_time(snap["time_s"])
        controller.decide(snap)
        snap = sim.step()

    event = create_event(sc.event_code, severity=sc.severity)
    sim.inject_event(event, severity=sc.severity)

    event_end = warmup_s + event.duration_s()
    recovery_window = 30.0
    prev_filtered = sim.filtered_net_kw
    min_battery = sim.battery_pct
    total_variation = 0.0
    load_shed = 0.0
    shed_transitions = 0
    prev_states = {"resort": "NORMAL", "residential": "NORMAL", "desalination": "NORMAL"}
    total_ticks = 0
    served_ticks = 0
    water_sum = 0.0

    # disruption weights for load shed: cutting a HIGH-criticality resource
    # (water, homes) is far more disruptive than shedding the low-priority
    # resort, so shed energy is weighted by how harmful it is to the island.
    disrupt_weight = {"resort": 0.2, "residential": 0.7, "desalination": 0.9}

    # recovery tracking: time the island spends out of balance — measured from
    # the moment the filtered balance first drops below -5 until it returns to
    # >= -5 and stays there for a sustained period. Rewards controllers that
    # keep the dip shallow and rebalance quickly.
    disturbed_at = None
    rebalanced_at = None
    stable_since = None

    window_ticks = int((event.duration_s() + recovery_window) / sim.dt)
    for _ in range(window_ticks):
        snap = sim.snapshot()
        controller.set_time(snap["time_s"])
        controller.decide(snap)
        snap = sim.step()

        eb = snap["energy_balance"]
        filtered = eb["filtered_kw"]
        net = eb["net_kw"]
        battery = snap["battery"]["pct"]
        resources = {r["id"]: r for r in snap["resources"]}

        total_ticks += 1
        min_battery = min(min_battery, battery)

        # unserved capacity (battery empty + deficit)
        served = (battery > 0.5) or (net >= -1e-6)
        if served:
            served_ticks += 1
            water_sum += resources["desalination"]["operating_pct"] / 100.0

        # disruptive load shed: energy removed from flexible consumers,
        # weighted by how disruptive cutting each resource is (higher priority
        # = more harmful). Shedding the low-priority resort is far cheaper
        # than throttling water or cutting residential demand.
        for rid in ("resort", "residential", "desalination"):
            r = resources[rid]
            possible = r["possible_demand_kw"]
            actual = r["actual_kw"]
            if possible > 0:
                load_shed += (possible - actual) * sim.dt * disrupt_weight[rid]

        # shedding event: count every time a flexible resource drops out of
        # normal operation (abrupt shed or deep reduction).
        for rid in ("resort", "residential"):
            st = resources[rid]["state"]
            if st in ("SHED", "REDUCED", "COOLDOWN") and prev_states[rid] == "NORMAL":
                shed_transitions += 1
            prev_states[rid] = st

        # instability: total variation of filtered net
        total_variation += abs(filtered - prev_filtered)
        prev_filtered = filtered

        # recovery: how long the island stays out of balance.
        if disturbed_at is None and filtered < -5.0:
            disturbed_at = snap["time_s"]
        if disturbed_at is not None and rebalanced_at is None:
            if filtered >= -5.0:
                if stable_since is None:
                    stable_since = snap["time_s"]
                elif (snap["time_s"] - stable_since) >= 2.0:
                    rebalanced_at = snap["time_s"]
                    break
            else:
                stable_since = None

    res.total_ticks = total_ticks
    res.served_ticks = served_ticks
    res.critical_uptime = served_ticks / total_ticks if total_ticks else 0.0
    res.water_availability = water_sum / total_ticks if total_ticks else 0.0
    res.total_load_shed_kwh = load_shed
    res.shed_events = shed_transitions
    res.min_battery_pct = min_battery
    res.instability = total_variation / total_ticks  # avg |Δfiltered| per tick
    if disturbed_at is None:
        res.recovery_time_s = 0.0
    elif rebalanced_at is None:
        res.recovery_time_s = recovery_window
    else:
        res.recovery_time_s = rebalanced_at - disturbed_at
    res.score = compute_score(res)
    return res


def compute_score(res: ScenarioResult,
                  weights: Dict[str, float] = None) -> float:
    """Prototype Nimbus score. Weights are configurable, not claimed optimal."""
    w = weights or {
        "critical": 25.0,
        "water": 10.0,
        "stability": 35.0,
        "battery": 22.0,
        "recovery": 8.0,
    }
    critical = res.critical_uptime * w["critical"]
    water = res.water_availability * w["water"]

    # stability: reward low oscillation (small avg |Δfiltered| per tick)
    stability_raw = max(0.0, 1.0 - res.instability / 8.0)
    stability = stability_raw * w["stability"]

    battery = (res.min_battery_pct / 100.0) * w["battery"]

    # recovery: faster is better (scale relative to ~25s budget)
    recovery_frac = max(0.0, min(1.0, 1.0 - res.recovery_time_s / 25.0))
    recovery = recovery_frac * w["recovery"]

    return round(critical + water + stability + battery + recovery, 1)


class Evaluator:
    def __init__(self, n_scenarios: int = 120, seed: int = 7,
                 config: NimbusConfig = DEFAULT_CONFIG):
        self.n = n_scenarios
        self.seed = seed
        self.cfg = config
        self.scenarios: List[Scenario] = []
        self.results: Dict[str, List[ScenarioResult]] = {}
        self.controllers = {
            "naive": NaiveController,
            "reactive": ReactiveController,
            "nimbus": NimbusController,
        }
        self.aggregates: Dict[str, dict] = {}

    def run(self) -> Dict[str, dict]:
        rng = random.Random(self.seed)
        self.scenarios = [Scenario(i, rng) for i in range(self.n)]

        controller_names = list(self.controllers.keys())
        self.results = {name: [] for name in controller_names}

        for sc in self.scenarios:
            sim = IslandSim(self.cfg)
            for name in controller_names:
                expl = ExplainabilityEngine()
                ctrl = self.controllers[name](self.cfg)
                res = run_scenario(sim, ctrl, sc, expl)
                self.results[name].append(res)

        self._compute_aggregates()
        return self.aggregates

    def _compute_aggregates(self):
        for name, results in self.results.items():
            if not results:
                continue
            agg = {
                "controller": name,
                "critical_uptime": self._avg(r.critical_uptime for r in results),
                "water_availability": self._avg(r.water_availability for r in results),
                "total_load_shed_kwh": self._avg(r.total_load_shed_kwh for r in results),
                "shed_events": self._avg(r.shed_events for r in results),
                "recovery_time_s": self._avg(r.recovery_time_s for r in results),
                "min_battery_pct": self._avg(r.min_battery_pct for r in results),
                "instability": self._avg(r.instability for r in results),
                "score": self._avg(r.score for r in results),
            }
            self.aggregates[name] = agg

    def summary_rows(self) -> List[dict]:
        order = ["naive", "reactive", "nimbus"]
        return [self.aggregates[name] for name in order if name in self.aggregates]

    @staticmethod
    def _avg(it) -> float:
        vals = list(it)
        return round(sum(vals) / len(vals), 3) if vals else 0.0


def run_evaluation(n: int = 120, seed: int = 7) -> Dict[str, dict]:
    return Evaluator(n_scenarios=n, seed=seed).run()
