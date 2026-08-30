"""
Nimbus — island resource manager.

Holds the live runtime state of every island resource (hospital, desalination,
residential, resort) and exposes the current demand / operating level used by
the simulation and controllers.
"""

from typing import Dict, List

from models import RESOURCE_DEFS, ResourceDef


# Human-readable state labels used on the dashboard.
STATE_LABELS = {
    "PROTECTED": "protected",
    "NORMAL": "normal",
    "THROTTLED": "throttled",
    "REDUCED": "reduced",
    "SHED": "shed",
    "COOLDOWN": "cooling down",
}


class Resource:
    def __init__(self, resdef: ResourceDef):
        self.defn = resdef
        self.operating_pct = 100.0
        self.state = "PROTECTED" if resdef.id == "hospital" else "NORMAL"
        self.demand_offset_kw = 0.0       # set by event engine / manual sliders

    @property
    def id(self) -> str:
        return self.defn.id

    @property
    def possible_demand_kw(self) -> float:
        """Power the resource wants when running at 100%."""
        return self.defn.base_demand_kw + self.demand_offset_kw

    @property
    def actual_kw(self) -> float:
        """Power actually consumed given the current operating level."""
        return self.possible_demand_kw * self.operating_pct / 100.0

    def reset(self):
        self.operating_pct = 100.0
        self.state = "PROTECTED" if self.defn.id == "hospital" else "NORMAL"
        self.demand_offset_kw = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.defn.name,
            "criticality": self.defn.criticality,
            "continuously_throttleable": self.defn.continuously_throttleable,
            "shed_capable": self.defn.shed_capable,
            "min_operating_pct": self.defn.min_operating_pct,
            "operating_pct": round(self.operating_pct, 1),
            "state": self.state,
            "state_label": STATE_LABELS.get(self.state, self.state.lower()),
            "possible_demand_kw": round(self.possible_demand_kw, 1),
            "actual_kw": round(self.actual_kw, 1),
        }


class ResourceManager:
    def __init__(self):
        self.resources: Dict[str, Resource] = {
            r.id: Resource(r) for r in RESOURCE_DEFS
        }
        self._order = [r.id for r in RESOURCE_DEFS]

    def __getitem__(self, rid: str) -> Resource:
        return self.resources[rid]

    def get(self, rid: str) -> Resource:
        return self.resources[rid]

    def reset(self):
        for r in self.resources.values():
            r.reset()

    def apply_demand_offsets(self, offsets: Dict[str, float]):
        # Any resource not mentioned is reset to no offset, so clearing
        # overrides/ending an event always returns demand to its base level.
        for rid, res in self.resources.items():
            res.demand_offset_kw = offsets.get(rid, 0.0)

    @property
    def total_demand_kw(self) -> float:
        return sum(r.actual_kw for r in self.resources.values())

    @property
    def hospital_kw(self) -> float:
        return self.resources["hospital"].actual_kw

    def as_list(self) -> List[dict]:
        return [self.resources[rid].to_dict() for rid in self._order]

    def set_operating(self, rid: str, pct: float, state: str):
        res = self.resources[rid]
        res.operating_pct = max(0.0, min(100.0, pct))
        res.state = state
