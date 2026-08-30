"""
Nimbus — island microgrid simulation.

This is a software prototype / simulation. The physics are intentionally simple
but internally consistent: solar + wind generation, a battery, and four island
resources whose demand is controlled by an autonomous controller each tick.

Every ~100 ms (dt) the simulation advances time, applies any active event,
computes the energy balance, updates the battery, and recomputes the filtered
energy-balance trajectory (velocity and acceleration) used for early detection.
"""

import math
import random
from typing import Dict, Optional

from models import DEFAULT_CONFIG, NimbusConfig
from resource_manager import ResourceManager


class IslandSim:
    def __init__(self, config: Optional[NimbusConfig] = None):
        self.cfg = config or DEFAULT_CONFIG
        self.reset()

    # ------------------------------------------------------------------ reset
    def reset(self, battery_pct: Optional[float] = None):
        self.t = 0.0
        self.dt = self.cfg.dt_s
        self.rng = random.Random(1234)  # deterministic variation

        self.battery_kwh = self.cfg.battery_capacity_kwh * (battery_pct or self.cfg.battery_init_pct) / 100.0
        self.battery_power_kw = 0.0     # + = charging, - = discharging
        self.residual_net_kw = 0.0      # imbalance the battery could not absorb

        self.resources = ResourceManager()
        self.event = None
        self.event_t0 = None
        self.current_event_code = None

        # generation
        self.solar_kw = self.cfg.solar_max_kw * 0.72
        self.wind_kw = self.cfg.wind_max_kw * 0.5
        self.solar_mult = 1.0
        self.wind_mult = 1.0

        # energy balance & trajectory signals
        self.net_kw = 0.0
        self.filtered_net_kw = 0.0
        self.velocity_kw_s = 0.0
        self.accel_kw_s2 = 0.0
        self._prev_filtered = 0.0
        self._prev_velocity = 0.0
        self._smoothed_velocity = 0.0

        # manual overrides from dashboard sliders (None = automatic)
        self.manual: Dict[str, Optional[float]] = {
            "solar": None, "wind": None, "battery_pct": None,
            "residential": None, "desalination": None, "resort": None,
        }

    # ----------------------------------------------------------- generation
    def _base_generation(self):
        # slowly drifting, slightly noisy renewable base
        solar = self.cfg.solar_max_kw * (0.72 + 0.06 * math.sin(self.t / 45.0)
                                         + 0.015 * self.rng.uniform(-1, 1))
        wind = self.cfg.wind_max_kw * (0.50 + 0.14 * math.sin(self.t / 13.0 + 2.0)
                                       + 0.03 * self.rng.uniform(-1, 1))
        return solar, wind

    def _generation(self):
        base_solar, base_wind = self._base_generation()
        if self.manual["solar"] is not None:
            solar = self.manual["solar"]
        else:
            solar = base_solar * self.solar_mult
        if self.manual["wind"] is not None:
            wind = self.manual["wind"]
        else:
            wind = base_wind * self.wind_mult
        solar = max(0.0, min(self.cfg.solar_max_kw, solar))
        wind = max(0.0, min(self.cfg.wind_max_kw, wind))
        return solar, wind

    # ------------------------------------------------------------- event loop
    def inject_event(self, event, severity: float = 1.0):
        """Start an event. If severity provided it overwrites the event's."""
        if severity is not None:
            event.severity = severity
        self.event = event
        self.event_t0 = self.t
        self.current_event_code = event.code

    def clear_event(self):
        self.event = None
        self.event_t0 = None
        self.current_event_code = None

    def _update_event(self):
        """Apply the active event's multipliers + demand offsets, then overlay
        manual slider overrides on top. Runs every tick so the manual demand
        sliders work even when no event is active."""
        offsets = {}
        if self.event is None:
            self.solar_mult, self.wind_mult = 1.0, 1.0
        else:
            elapsed = self.t - self.event_t0
            solar_mult, wind_mult, ev_offsets = self.event.effects_at(elapsed)
            self.solar_mult = solar_mult
            self.wind_mult = wind_mult
            offsets = dict(ev_offsets)
            if self.event.is_finished(elapsed):
                self.clear_event()
        # Manual demand overrides are absolute kW targets; they take
        # precedence over any event demand offsets.
        for key, rid in (("residential", "residential"),
                         ("desalination", "desalination"),
                         ("resort", "resort")):
            if self.manual[key] is not None:
                res = self.resources[rid]
                offsets[rid] = self.manual[key] - res.defn.base_demand_kw
        self.resources.apply_demand_offsets(offsets)

    # -------------------------------------------------------------- battery
    def _battery_step(self, net_kw, dt):
        b_cap = self.cfg.battery_capacity_kwh
        if net_kw >= 0:
            max_charge = min(self.cfg.max_charge_rate_kw,
                             (b_cap - self.battery_kwh) / dt if dt > 0 else 0)
            charge = max(0.0, min(net_kw, max_charge)) if max_charge > 0 else 0.0
            self.battery_kwh += charge * dt
            self.battery_power_kw = charge
        else:
            max_discharge = min(self.cfg.max_discharge_rate_kw,
                                self.battery_kwh / dt if dt > 0 else 0)
            discharge = max(0.0, min(-net_kw, max_discharge)) if max_discharge > 0 else 0.0
            self.battery_kwh -= discharge * dt
            self.battery_power_kw = -discharge
        self.battery_kwh = max(0.0, min(b_cap, self.battery_kwh))
        self.residual_net_kw = net_kw - self.battery_power_kw

    @property
    def battery_pct(self) -> float:
        return 100.0 * self.battery_kwh / self.cfg.battery_capacity_kwh

    # ------------------------------------------------------------- telemetry
    def step(self, dt: Optional[float] = None) -> dict:
        """Advance physics one control tick and return a telemetry snapshot.

        NOTE: does NOT run the controller. The controller is expected to set
        resource operating levels based on the previous snapshot, which then
        feed into this tick's demand.
        """
        if dt is not None:
            self.dt = dt
        dt = self.dt
        self.t += dt

        if self.manual["battery_pct"] is not None:
            self.battery_kwh = self.cfg.battery_capacity_kwh * self.manual["battery_pct"] / 100.0

        self._update_event()
        self.solar_kw, self.wind_kw = self._generation()

        total_demand = self.resources.total_demand_kw
        net = self.solar_kw + self.wind_kw - total_demand
        self.net_kw = net
        self._battery_step(net, dt)

        # filtered energy-balance trajectory
        alpha = 1.0 - math.exp(-dt / self.cfg.ema_tau_s)
        prev_filtered = self.filtered_net_kw
        self.filtered_net_kw += alpha * (net - self.filtered_net_kw)
        self.velocity_kw_s = (self.filtered_net_kw - prev_filtered) / dt
        # lightly smooth velocity before computing acceleration
        self._smoothed_velocity += 0.5 * (self.velocity_kw_s - self._smoothed_velocity)
        self.accel_kw_s2 = (self._smoothed_velocity - self._prev_velocity) / dt
        self._prev_filtered = self.filtered_net_kw
        self._prev_velocity = self._smoothed_velocity

        return self.snapshot()

    def snapshot(self) -> dict:
        solar = self.solar_kw
        wind = self.wind_kw
        total_gen = solar + wind
        total_demand = self.resources.total_demand_kw

        return {
            "timestamp": round(self.t, 2),
            "time_s": round(self.t, 2),
            "event": self.current_event_code or "none",
            "generation": {
                "solar_kw": round(solar, 1),
                "wind_kw": round(wind, 1),
                "total_kw": round(total_gen, 1),
                "solar_mult": round(self.solar_mult, 2),
                "wind_mult": round(self.wind_mult, 2),
            },
            "battery": {
                "kwh": round(self.battery_kwh, 1),
                "pct": round(self.battery_pct, 1),
                "capacity_kwh": self.cfg.battery_capacity_kwh,
                "power_kw": round(self.battery_power_kw, 1),
            },
            "demand": {
                "hospital_kw": round(self.resources["hospital"].actual_kw, 1),
                "desalination_kw": round(self.resources["desalination"].actual_kw, 1),
                "residential_kw": round(self.resources["residential"].actual_kw, 1),
                "resort_kw": round(self.resources["resort"].actual_kw, 1),
                "total_kw": round(total_demand, 1),
            },
            "energy_balance": {
                "net_kw": round(self.net_kw, 1),
                "filtered_kw": round(self.filtered_net_kw, 1),
                "velocity_kw_s": round(self.velocity_kw_s, 2),
                "acceleration_kw_s2": round(self.accel_kw_s2, 2),
                "residual_kw": round(self.residual_net_kw, 1),
                "battery_power_kw": round(self.battery_power_kw, 1),
            },
            "resources": self.resources.as_list(),
        }
