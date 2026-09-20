"""Causal damped Newton-Raphson estimator for directional cycle-phase targets."""
from __future__ import annotations
from collections import defaultdict, deque
import math
from typing import Deque, Dict

def _wrap(angle: float) -> float:
    return (angle + 180.0) % 360.0 - 180.0

class NewtonPhaseSetpoints:
    """One rolling phase target per (symbol, BUY/SELL) confirmed-turn cohort."""
    def __init__(self, window: int = 60, max_step_degrees: float = 10.0, minimum_samples: int = 5) -> None:
        self.window, self.max_step, self.minimum_samples = int(window), abs(float(max_step_degrees)), int(minimum_samples)
        self._samples: Dict[tuple[str,str], Deque[float]] = defaultdict(lambda: deque(maxlen=self.window))
        self._targets: Dict[tuple[str,str], float] = {}
    def target(self, symbol: str, side: str) -> float | None:
        return self._targets.get((symbol,side))
    def update(self, symbol: str, side: str, confirmed_turn_phase: float) -> dict:
        key=(symbol,side); values=self._samples[key]; values.append(float(confirmed_turn_phase))
        old=self._targets.get(key, 0.0)
        if len(values)<self.minimum_samples:
            return {"side":side,"samples":len(values),"target_before":old,"target_after":old,"updated":False}
        diffs=[math.radians(_wrap(x-old)) for x in values]
        f=sum(math.sin(x) for x in diffs)/len(diffs); derivative=-sum(math.cos(x) for x in diffs)/len(diffs)
        if abs(derivative)<1e-6:
            return {"side":side,"samples":len(values),"target_before":old,"target_after":old,"updated":False,"reason":"singular_derivative"}
        step=max(-self.max_step,min(self.max_step,-math.degrees(f/derivative)))
        new=_wrap(old+step); self._targets[key]=new
        return {"side":side,"samples":len(values),"target_before":old,"target_after":new,"newton_residual":f,"newton_derivative":derivative,"step_degrees":step,"updated":True}
