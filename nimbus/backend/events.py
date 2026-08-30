"""
Nimbus — event engine.

Injectable disturbances that shift the live environmental/demand sensor data.
Every event has an attack, sustain and recover phase so changes feel gradual
and physically plausible rather than instantaneous.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional

from models import DEFAULT_CONFIG


def _lerp(a: float, b: float, f: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, f))


@dataclass
class Event:
    """A single disturbance that can be injected into the island."""

    code: str
    name: str
    description: str
    solar_mult: float            # multiplier applied to solar during full effect
    wind_mult: float             # multiplier applied to wind during full effect
    demand_add_kw: Dict[str, float]   # extra demand per resource during full effect
    attack_s: float = 1.5
    sustain_s: float = 8.0
    recover_s: float = 5.0
    severity: float = 1.0        # scales the severity of the effect

    def duration_s(self) -> float:
        return self.attack_s + self.sustain_s + self.recover_s

    def effects_at(self, t_elapsed: float):
        """Return (solar_mult, wind_mult, demand_offsets) at time-since-inject."""
        if t_elapsed < 0:
            f = 0.0
        elif t_elapsed < self.attack_s:
            f = t_elapsed / self.attack_s            # attack ramp
        elif t_elapsed < self.attack_s + self.sustain_s:
            f = 1.0                                   # sustain
        else:
            recover_progress = (t_elapsed - self.attack_s - self.sustain_s) / self.recover_s
            f = max(0.0, 1.0 - recover_progress)      # recover
        solar = self._apply_severity_mult(1.0, self.solar_mult, f)
        wind = self._apply_severity_mult(1.0, self.wind_mult, f)
        offsets = {rid: v * f for rid, v in self.demand_add_kw.items()}
        return solar, wind, offsets

    def _apply_severity_mult(self, neutral: float, worst: float, f: float) -> float:
        # blend neutral (1.0) with worst, biased by severity
        target = neutral + (worst - neutral) * self.severity
        return _lerp(1.0, target, f)

    def is_finished(self, t_elapsed: float) -> bool:
        return t_elapsed >= self.attack_s + self.sustain_s + self.recover_s


def create_event(code: str, severity: float = 1.0) -> Event:
    """Build a standard event. `severity` in [0..1] scales its effect."""
    severity = max(0.0, min(1.0, severity))
    catalog = {
        "storm": Event(
            code="storm", name="Storm",
            description="Large rapid reduction in solar and wind.",
            solar_mult=0.15, wind_mult=0.35, demand_add_kw={},
            attack_s=1.5, sustain_s=8.0, recover_s=5.0,
        ),
        "cloud_cover": Event(
            code="cloud_cover", name="Cloud Cover",
            description="Moderate solar reduction over several seconds.",
            solar_mult=0.5, wind_mult=1.0, demand_add_kw={},
            attack_s=3.0, sustain_s=6.0, recover_s=6.0,
        ),
        "wind_drop": Event(
            code="wind_drop", name="Wind Drop",
            description="Rapid reduction in wind generation.",
            solar_mult=1.0, wind_mult=0.25, demand_add_kw={},
            attack_s=1.0, sustain_s=6.0, recover_s=4.0,
        ),
        "tourist_surge": Event(
            code="tourist_surge", name="Tourist Surge",
            description="Sudden increase in residential & resort demand.",
            solar_mult=1.0, wind_mult=1.0,
            demand_add_kw={"residential": 40.0, "resort": 45.0},
            attack_s=2.0, sustain_s=8.0, recover_s=4.0,
        ),
        "water_emergency": Event(
            code="water_emergency", name="Water Emergency",
            description="Sudden increase in desalination demand.",
            solar_mult=1.0, wind_mult=1.0,
            demand_add_kw={"desalination": 60.0},
            attack_s=2.0, sustain_s=8.0, recover_s=4.0,
        ),
        "compound_crisis": Event(
            code="compound_crisis", name="Compound Crisis",
            description="Simultaneous renewable loss + demand increase.",
            solar_mult=0.3, wind_mult=0.4,
            demand_add_kw={"residential": 25.0, "resort": 20.0},
            attack_s=2.0, sustain_s=8.0, recover_s=5.0,
        ),
    }
    ev = catalog[code]
    ev.severity = severity
    return ev


EVENT_CODES = ["storm", "cloud_cover", "wind_drop",
               "tourist_surge", "water_emergency", "compound_crisis"]

EVENT_META = {code: {"name": create_event(code).name,
                     "description": create_event(code).description}
              for code in EVENT_CODES}
