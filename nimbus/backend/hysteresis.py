"""
Nimbus — hysteresis utilities.

Hysteresis uses two different thresholds for entering and exiting a state so a
controller does not rapidly flap when a noisy signal sits near a single
threshold.
"""


class HysteresisBand:
    """A comparator with hysteresis around a low-trigger threshold.

    state becomes True (active) when `value` drops to or below `lower`;
    it becomes False (inactive) only when `value` rises to or above `upper`.
    This is the classic use for "shed when battery gets low, restore only after
    it recovers well above the trigger".
    """

    def __init__(self, lower: float, upper: float, initial: bool = False):
        assert upper >= lower, "upper must be >= lower"
        self.lower = lower
        self.upper = upper
        self.state = initial

    def update(self, value: float) -> bool:
        if not self.state and value <= self.lower:
            self.state = True
        elif self.state and value >= self.upper:
            self.state = False
        return self.state

    def reset(self, state: bool = False):
        self.state = state


class FallingBand:
    """Enter 'low' when a signal falls below `lower`; exit when it recovers
    above `upper`. Use for a signal like net power where low means shortage."""

    def __init__(self, lower: float, upper: float):
        assert upper >= lower, "upper must be >= lower"
        self.lower = lower
        self.upper = upper
        self.low = False

    def update(self, value: float) -> bool:
        if not self.low and value < self.lower:
            self.low = True
        elif self.low and value >= self.upper:
            self.low = False
        return self.low

    def reset(self):
        self.low = False
