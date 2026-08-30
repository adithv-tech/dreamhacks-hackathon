"""
Nimbus — explainability engine.

Every major Nimbus action produces a human-readable explanation so a judge can
understand the reasoning without knowing control theory. This module turns raw
decision records into structured reasons shown in the "WHY?" panel.
"""

import time
from typing import Dict, List, Optional


class DecisionRecord:
    def __init__(self, controller: str, kind: str, title: str, context: dict,
                 effects: List[str], reason: str):
        self.controller = controller
        self.kind = kind
        self.title = title
        self.context = context
        self.effects = effects
        self.reason = reason
        self.t = time.time()

    def to_dict(self) -> dict:
        return {
            "controller": self.controller,
            "kind": self.kind,
            "title": self.title,
            "context": self.context,
            "effects": self.effects,
            "reason": self.reason,
        }


class ExplainabilityEngine:
    """Rolling log of autonomous decisions plus a query builder for reasons."""

    def __init__(self, max_log: int = 100):
        self.log: List[DecisionRecord] = []
        self.max_log = max_log
        self.latest: Optional[dict] = None

    def reset(self):
        self.log.clear()
        self.latest = None

    def record(self, controller: str, kind: str, title: str, context: dict,
               effects: List[str], reason: str):
        rec = DecisionRecord(controller, kind, title, context, effects, reason)
        self.log.append(rec)
        if len(self.log) > self.max_log:
            self.log = self.log[-self.max_log:]
        self.latest = rec.to_dict()

    # ---- friendly reason builders -------------------------------------
    def storm_detected(self, accel: float, desal_before: float, desal_after: float) -> dict:
        context = {"acceleration_kw_s2": accel}
        effects = [
            "Hospital protected",
            f"Desalination reduced {desal_before:.0f}% → {desal_after:.0f}%",
        ]
        reason = (
            f"Rapid generation loss detected (energy-balance acceleration "
            f"{accel:.1f} kW/s²). Battery stable but deteriorating rapidly. "
            f"Desalination throttled to protect critical services."
        )
        return {"kind": "throttle_desalination", "title": "Why did Nimbus throttle desalination?",
                "context": context, "effects": effects, "reason": reason}

    def resort_shed(self, gen_drop_pct: float, battery_pct: float) -> dict:
        context = {"generation_drop_pct": gen_drop_pct, "battery_pct": battery_pct}
        effects = [
            "Hospital protected",
            "Water services protected",
            "Residential demand preserved",
            "Lowest-priority flexible load shed",
        ]
        reason = (
            f"Renewable generation fell {gen_drop_pct:.0f}%. Battery reserve "
            f"approaching safety threshold ({battery_pct:.0f}%) and trajectory "
            f"deteriorating. Resort — the lowest-priority flexible load — shed."
        )
        return {"kind": "shed_resort", "title": "Why was the resort shed?",
                "context": context, "effects": effects, "reason": reason}

    def resort_restore(self, recovered_s: float, battery_pct: float) -> dict:
        context = {"recovered_s": recovered_s, "battery_pct": battery_pct}
        effects = [
            "Resort restored gradually",
            "Generation recovered",
            "Energy balance stable",
        ]
        reason = (
            f"Generation recovered for {recovered_s:.1f}s. Battery reserve "
            f"increasing ({battery_pct:.0f}%), energy balance stable, cooldown "
            f"completed. Resort restored gradually."
        )
        return {"kind": "restore_resort", "title": "Why was the resort restored?",
                "context": context, "effects": effects, "reason": reason}

    def residential_reduce(self, net_kw: float, from_pct: float, to_pct: float) -> dict:
        context = {"net_kw": net_kw}
        effects = [
            "Hospital protected",
            "Desalination kept running",
            f"Residential demand reduced {from_pct:.0f}% → {to_pct:.0f}%",
        ]
        reason = (
            f"Energy deficit deepened ({net_kw:.0f} kW). After protecting the "
            f"hospital and water plant, Nimbus reduced medium-priority "
            f"residential demand."
        )
        return {"kind": "reduce_residential", "title": "Why was residential demand reduced?",
                "context": context, "effects": effects, "reason": reason}
