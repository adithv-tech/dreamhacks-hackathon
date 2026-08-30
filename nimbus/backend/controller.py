"""
Nimbus — autonomous controllers.

Three controller implementations share one interface: given the latest island
telemetry snapshot, each decides the new operating level / state of every
resource and records a human-readable explanation for every major action.

  1. Naive      — only reacts to battery percentage. No trajectory, no
                  continuous desalination control, no cooldown. Intentionally
                  simple for the visual demo.
  2. Reactive   — reacts to battery + current net power with hysteresis and a
                  simple P-based desalination controller. No velocity /
                  acceleration early detection. A credible baseline.
  3. Nimbus     — early detection from the filtered energy-balance trajectory
                  (velocity + acceleration), PD control of the desalination
                  plant, priority-aware hierarchy, hysteresis and an orderly
                  restoration state machine.

These are prototype controllers for a simulated island, not grid hardware.
"""

import math
from typing import Optional

from models import DEFAULT_CONFIG, NimbusConfig
from resource_manager import ResourceManager
from explainability import ExplainabilityEngine
from hysteresis import HysteresisBand, FallingBand


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class ShedStateMachine:
    """NORMAL -> SHED -> COOLDOWN -> NORMAL with anti-flap protection."""

    def __init__(self, rid: str, cooldown_s: float):
        self.rid = rid
        self.cooldown_s = cooldown_s
        self.state = "NORMAL"
        self.shed_at_s = None
        self.cooldown_until_s = None

    def update(self, t_s: float, shed_condition: bool,
               recover_condition: bool) -> str:
        if self.state == "NORMAL":
            if shed_condition:
                self.state = "SHED"
                self.shed_at_s = t_s
            return self.state

        if self.state == "SHED":
            if recover_condition:
                self.state = "COOLDOWN"
                self.cooldown_until_s = t_s + self.cooldown_s
            return self.state

        # COOLDOWN
        if t_s >= self.cooldown_until_s and recover_condition:
            self.state = "NORMAL"
            self.shed_at_s = None
            self.cooldown_until_s = None
        return self.state

    def reset(self):
        self.state = "NORMAL"
        self.shed_at_s = None
        self.cooldown_until_s = None


class BaseController:
    name = "base"
    label = "Base"
    description = ""

    def __init__(self, config: NimbusConfig = DEFAULT_CONFIG):
        self.cfg = config
        self.manager = None      # ResourceManager (set on bind)
        self.sim = None
        self.expl: Optional[ExplainabilityEngine] = None
        self._t = 0.0

    def bind(self, sim, expl: ExplainabilityEngine):
        self.sim = sim
        self.manager = sim.resources
        self.expl = expl
        self.reset()

    def reset(self):
        self._t = 0.0

    def set_time(self, t: float):
        self._t = t

    def decide(self, snap: dict):
        raise NotImplementedError

    # helpers -------------------------------------------------------------
    def set_op(self, rid: str, pct: float, state: str):
        self.manager.set_operating(rid, pct, state)

    def record(self, kind, title, context, effects, reason):
        if self.expl:
            self.expl.record(self.name, kind, title, context, effects, reason)


# ===========================================================================
# Baseline 1 — Naive threshold controller
# ===========================================================================
class NaiveController(BaseController):
    name = "naive"
    label = "Naive"
    description = "Reacts only to battery percentage. No trajectory, no smooth control."

    SHED_RESORT_BELOW = 30.0
    RESTORE_RESORT_ABOVE = 34.0
    SHED_RESIDENTIAL_BELOW = 20.0
    RESTORE_RESIDENTIAL_ABOVE = 24.0

    def __init__(self, config: NimbusConfig = DEFAULT_CONFIG):
        super().__init__(config)
        self._resort_shed = False
        self._residential_shed = False

    def reset(self):
        super().reset()
        self._resort_shed = False
        self._residential_shed = False

    def decide(self, snap: dict):
        battery = snap["battery"]["pct"]

        # --- resort: abrupt, single-threshold switching (no cooldown) ---
        if not self._resort_shed and battery < self.SHED_RESORT_BELOW:
            self._resort_shed = True
            self.set_op("resort", 0.0, "SHED")
            self.record("shed_resort", "Why was the resort shed?",
                        {"battery_pct": battery},
                        ["Hospital protected", "Resort shed (abruptly)"],
                        f"Battery fell below {self.SHED_RESORT_BELOW:.0f}%. "
                        f"Naive sheds the lowest-priority resort immediately.")
        elif self._resort_shed and battery >= self.RESTORE_RESORT_ABOVE:
            self._resort_shed = False
            self.set_op("resort", 100.0, "NORMAL")
            self.record("restore_resort", "Why was the resort restored?",
                        {"battery_pct": battery},
                        ["Resort restored to 100%"],
                        f"Battery recovered above {self.RESTORE_RESORT_ABOVE:.0f}%.")

        # --- residential: same abrupt style ---
        if not self._residential_shed and battery < self.SHED_RESIDENTIAL_BELOW:
            self._residential_shed = True
            self.set_op("residential", 0.0, "SHED")
            self.record("shed_residential", "Why was residential shed?",
                        {"battery_pct": battery},
                        ["Residential shed"],
                        f"Battery fell below {self.SHED_RESIDENTIAL_BELOW:.0f}%. "
                        f"Residential demand shed abruptly.")
        elif self._residential_shed and battery >= self.RESTORE_RESIDENTIAL_ABOVE:
            self._residential_shed = False
            self.set_op("residential", 100.0, "NORMAL")
            self.record("restore_residential", "Why was residential restored?",
                        {"battery_pct": battery},
                        ["Residential restored"],
                        "Battery recovered.")

        # desalination stays at 100% (naive has no smooth control)


# ===========================================================================
# Baseline 2 — Reactive controller (battery + net power + hysteresis)
# ===========================================================================
class ReactiveController(BaseController):
    name = "reactive"
    label = "Reactive"
    description = "Battery + current net power with hysteresis. No trajectory early-detection."

    def __init__(self, config: NimbusConfig = DEFAULT_CONFIG):
        super().__init__(config)
        self._desal_band = FallingBand(-45.0, -10.0)     # current deficit -> throttle
        self._res_band = HysteresisBand(30.0, 46.0)      # battery-based residential
        self._resort_band = HysteresisBand(22.0, 36.0)   # battery-based shed
        self._resort_sm = ShedStateMachine("resort", config.cooldown_s)
        self._prev_desal = 100.0
        # Reactive reacts to the CURRENT deficit and to the battery once it has
        # already been drained — there is no velocity/acceleration early
        # detection. It throttles desalination as soon as the net balance turns
        # negative and only sheds load after the battery is depleted.
        self._kp_reactive = 0.30

    def reset(self):
        super().reset()
        self._desal_band.reset()
        self._res_band.reset()
        self._resort_band.reset()
        self._resort_sm.reset()
        self._prev_desal = 100.0

    def decide(self, snap: dict):
        net = snap["energy_balance"]["net_kw"]          # current, unfiltered
        battery = snap["battery"]["pct"]
        t = snap["time_s"]

        # --- desalination: reacts only to the battery once it is depleted ---
        # (no trajectory early-detection, so it acts late, after the battery
        # has already drained)
        if battery < 65.0:
            frac = (65.0 - battery) / 65.0
            target = 100.0 - (100.0 - self.cfg.desal_min_pct) * frac
        else:
            target = 100.0
        out = self._prev_desal + clamp(target - self._prev_desal, -15.0, 15.0)
        self._prev_desal = out
        self.set_op("desalination", out, "THROTTLED" if out < 99.0 else "NORMAL")

        # --- residential reduction: reacts to the battery once it is low ---
        res_low = self._res_band.update(battery)
        if res_low:
            self.set_op("residential", 40.0, "REDUCED")
        else:
            self.set_op("residential", 100.0, "NORMAL")

        # --- resort: battery hysteresis + cooldown ---
        # Reactive waits until the battery has already been drained.
        shed_cond = self._resort_band.update(battery)
        recover_cond = battery >= 36.0
        st = self._resort_sm.update(t, shed_cond, recover_cond)
        if st == "SHED":
            self.set_op("resort", 0.0, "SHED")
        elif st == "COOLDOWN":
            self.set_op("resort", 0.0, "COOLDOWN")
        else:
            self.set_op("resort", 100.0, "NORMAL")


# ===========================================================================
# Nimbus controller — early detection + priority-aware orchestration
# ===========================================================================
class NimbusController(BaseController):
    name = "nimbus"
    label = "Nimbus"
    description = ("Early trajectory detection (velocity/acceleration), PD "
                   "desalination control, criticality hierarchy, hysteresis, "
                   "orderly restoration.")

    def __init__(self, config: NimbusConfig = DEFAULT_CONFIG):
        super().__init__(config)
        self._desal_prev = 100.0
        self._res_band = FallingBand(-95.0, -20.0)     # severe deficit -> reduce homes
        self._resort_sm = ShedStateMachine("resort", config.cooldown_s)
        self._resort_shed_state = "NORMAL"
        self._shed_logged = False
        self._recover_started = None

    def reset(self):
        super().reset()
        self._desal_prev = 100.0
        self._res_band.reset()
        self._resort_sm.reset()
        self._resort_shed_state = "NORMAL"
        self._shed_logged = False
        self._desal_logged = False
        self._prev_eff = 0.0

    def _projected_net_kw(self, snap) -> float:
        eb = snap["energy_balance"]
        return (eb["filtered_kw"]
                + eb["velocity_kw_s"] * self.cfg.lead_s
                + 0.5 * eb["acceleration_kw_s2"] * self.cfg.accel_lead_s ** 2)

    def decide(self, snap: dict):
        eb = snap["energy_balance"]
        battery = snap["battery"]["pct"]
        filtered = eb["filtered_kw"]
        velocity = eb["velocity_kw_s"]
        accel = eb["acceleration_kw_s2"]
        t = snap["time_s"]

        gen = snap["generation"]
        solar_mult = gen["solar_mult"]
        wind_mult = gen["wind_mult"]

        # ------------------------------------------------------------------
        # Stage 1 — Critical services (hospital) are always protected. It is
        # never throttled or shed, and is left untouched here.

        # ------------------------------------------------------------------
        # Stage 2 — Lowest-priority resort shed first, using early trajectory
        # detection (velocity + acceleration) so we act as the balance starts
        # deteriorating rather than after the battery is drained. Cooldown
        # prevents rapid shed/restore flapping.
        # ------------------------------------------------------------------
        projected = self._projected_net_kw(snap)
        shed_condition = (
            battery < self.cfg.battery_critical_pct
            or projected < self.cfg.projected_shed_net_kw
        )
        recover_condition = (
            battery >= self.cfg.restore_battery_pct
            and projected > self.cfg.resort_recover_net_kw
        )
        st = self._resort_sm.update(t, shed_condition, recover_condition)
        self._resort_shed_state = st
        if st == "SHED":
            self._set_smooth("resort", 0.0, "SHED")
            if not self._shed_logged:
                self._shed_logged = True
                drop = self._gen_drop_pct(solar_mult, wind_mult)
                self.record("shed_resort", "Why was the resort shed?",
                            {"projected_kw": projected, "battery_pct": battery},
                            ["Hospital protected",
                             "Water services protected",
                             "Residential demand preserved",
                             "Lowest-priority flexible load shed"],
                            (f"Early detection: renewable generation down "
                             f"{drop:.0f}%, projected energy balance "
                             f"{projected:.0f} kW crossing the safety threshold. "
                             f"Hospital and water protected; lowest-priority "
                             f"resort shed."))
        elif st == "COOLDOWN":
            self._set_smooth("resort", 0.0, "COOLDOWN")
            self._shed_logged = False
        else:
            # orderly, gradual restoration (never everything at once)
            self._set_smooth("resort", 100.0, "NORMAL", rate_per_s=30.0)
            self._shed_logged = False

        # ------------------------------------------------------------------
        # Stage 3 — Reduce medium-priority residential demand, but only when
        # the deficit is deep enough that shedding the resort is not enough,
        # or when the battery itself is at risk (hard safeguard so critical
        # services are never jeopardised).
        # ------------------------------------------------------------------
        low_res = self._res_band.update(filtered)
        battery_guard = battery < self.cfg.battery_critical_pct
        if low_res or battery_guard:
            self._set_smooth("residential", 40.0, "REDUCED")
        else:
            self._set_smooth("residential", 100.0, "NORMAL")

        # ------------------------------------------------------------------
        # Stage 4 — Desalination is throttled LAST, as a smooth last resort.
        # The PD term works on the residual deficit that resort + residential
        # shedding could not remove (a dead-zone protects water during mild
        # shortfalls). The derivative term uses the same error signal.
        # ------------------------------------------------------------------
        out = self._desal_last_resort(filtered)
        self._desal_prev = out
        desal_state = "THROTTLED" if out < 99.0 else "NORMAL"
        self.set_op("desalination", out, desal_state)

        # ------------------------------------------------------------------
        # Explainability
        # ------------------------------------------------------------------
        self._maybe_log_desal(out, accel, solar_mult, wind_mult)

    # ---- helpers ---------------------------------------------------------
    def _set_smooth(self, rid: str, target: float, state: str,
                    rate_per_s: float = 40.0):
        """Move a resource toward `target` at a bounded rate so shedding and
        restoration are gradual and never cause abrupt demand spikes."""
        res = self.manager.get(rid)
        step = rate_per_s * self.cfg.dt_s
        new = res.operating_pct + clamp(target - res.operating_pct, -step, step)
        self.manager.set_operating(rid, new, state)

    def _desal_last_resort(self, filtered):
        # raw deficit beyond the target, minus a dead-zone so mild shortfalls
        # (handled by resort + residential) do not touch water.
        raw_error = self.cfg.target_net_kw - filtered
        eff = max(0.0, raw_error - self.cfg.desal_deadzone_kw)
        # derivative on the SAME effective error
        dt = max(self.cfg.dt_s, 1e-6)
        derr = (eff - self._prev_eff) / dt
        self._prev_eff = eff
        pd = self.cfg.kp * eff + self.cfg.kd * derr
        # map PD effort onto the allowed physical range, smoothly
        full = max(self.cfg.kp * 80.0, 1e-6)   # a 80 kW deficit -> max throttle
        frac = clamp(pd / full, 0.0, 1.0)
        out = 100.0 - (100.0 - self.cfg.desal_min_pct) * frac
        return self._smooth(out)

    def _smooth(self, out):
        # limit per-tick movement so transitions are smooth (e.g. 100->87->73)
        limit = 15.0 * self.cfg.dt_s  # ~15 pct/sec
        return self._desal_prev + clamp(out - self._desal_prev, -limit, limit)

    def _gen_drop_pct(self, solar_mult, wind_mult):
        # generation drop relative to full renewables
        base = self.cfg.solar_max_kw + self.cfg.wind_max_kw
        cur = solar_mult * self.cfg.solar_max_kw + wind_mult * self.cfg.wind_max_kw
        if base <= 0:
            return 0.0
        return max(0.0, 100.0 * (base - cur) / base)

    def _maybe_log_desal(self, out, accel, solar_mult, wind_mult):
        if out < 96.0 and not getattr(self, "_desal_logged", False):
            self._desal_logged = True
            drop = self._gen_drop_pct(solar_mult, wind_mult)
            self.record("throttle_desalination",
                        "Why did Nimbus throttle desalination?",
                        {"acceleration_kw_s2": accel, "desal_pct": out},
                        ["Hospital protected",
                         f"Desalination reduced to {out:.0f}%"],
                        (f"Rapid generation loss detected (energy-balance "
                         f"acceleration {accel:.1f} kW/s², generation down "
                         f"{drop:.0f}%). Battery stable but deteriorating "
                         f"rapidly. Desalination throttled continuously; "
                         f"hospital protected."))
        elif out >= 96.0:
            self._desal_logged = False
