"""R8: explicit four-source directional consensus with separate execution ramp."""
from dataclasses import dataclass, asdict
from enum import Enum
import math


def finite(x: float) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise ValueError("expected finite number")
    return float(x)


def bounded(x: float) -> float:
    return max(-1.0, min(1.0, finite(x)))


class Vote(str, Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"


@dataclass(frozen=True)
class Evidence:
    governor: float
    avr: float
    dispatch: float
    studies: float
    actual_exposure: float = 0.0
    eligible: bool = True

    def __post_init__(self):
        for name in ("governor", "avr", "dispatch", "studies", "actual_exposure"):
            value = finite(getattr(self, name))
            if name != "actual_exposure" and abs(value) > 1:
                raise ValueError(f"{name} outside [-1, 1]")
        if type(self.eligible) is not bool:
            raise ValueError("eligible must be bool")

    def votes(self, threshold: float = 0.12) -> dict[str, Vote]:
        threshold = finite(threshold)
        if not 0 < threshold <= 1:
            raise ValueError("threshold outside (0, 1]")

        def vote(x):
            return Vote.BUY if x >= threshold else Vote.SELL if x <= -threshold else Vote.HOLD

        return {name: vote(getattr(self, name)) for name in ("governor", "avr", "dispatch", "studies")}


@dataclass(frozen=True)
class Decision:
    direction: Vote
    reason: str
    action_intent: str
    votes: dict[str, Vote]
    evidence: Evidence
    execution_target: float
    execution_command: float

    def as_dict(self):
        out = asdict(self)
        out["direction"] = self.direction.value
        out["votes"] = {k: v.value for k, v in self.votes.items()}
        return out


class R8Controller:
    """Decision authority for R8; execution remains a separate bounded ramp."""

    def __init__(self, threshold: float = 0.12, slew_per_second: float = 0.01):
        self.threshold = finite(threshold)
        self.slew_per_second = finite(slew_per_second)
        if not 0 < self.threshold <= 1 or self.slew_per_second <= 0:
            raise ValueError("invalid R8 limits")
        self.execution_command = 0.0

    def decide(self, evidence: Evidence) -> Decision:
        votes = evidence.votes(self.threshold)
        values = tuple(votes.values())
        long = evidence.actual_exposure > 0
        short = evidence.actual_exposure < 0

        if not evidence.eligible:
            direction, reason = Vote.HOLD, "INELIGIBLE_INPUT"
        elif all(v is Vote.BUY for v in values):
            direction, reason = Vote.BUY, "FOUR_WAY_BUY_CONSENSUS"
        elif all(v is Vote.SELL for v in values):
            direction, reason = Vote.SELL, "FOUR_WAY_SELL_CONSENSUS"
        elif long and votes["studies"] is Vote.SELL and votes["governor"] is Vote.SELL:
            direction, reason = Vote.SELL, "LONG_EXIT_GOVERNOR_STUDY_CONSENSUS"
        elif short and votes["studies"] is Vote.BUY and votes["governor"] is Vote.BUY:
            direction, reason = Vote.BUY, "SHORT_EXIT_GOVERNOR_STUDY_CONSENSUS"
        else:
            direction, reason = Vote.HOLD, "NO_DIRECTIONAL_CONSENSUS"

        if direction is Vote.BUY:
            intent = "ENTER_OR_MAINTAIN_LONG" if not short else "EXIT_SHORT"
            target = 1.0 if not short else 0.0
        elif direction is Vote.SELL:
            intent = "REDUCE_OR_EXIT_LONG" if long else "ENTER_SHORT_CANDIDATE"
            target = 0.0 if long else -1.0
        else:
            intent = "MAINTAIN_POSITION" if (long or short) else "STAY_FLAT"
            target = max(-1.0, min(1.0, evidence.actual_exposure))

        return Decision(direction, reason, intent, votes, evidence, target, self.execution_command)

    def ramp(self, target: float, actual: float, dt_seconds: float) -> float:
        target, actual, dt_seconds = bounded(target), finite(actual), finite(dt_seconds)
        if dt_seconds <= 0:
            raise ValueError("dt_seconds must be positive")
        step = self.slew_per_second * dt_seconds
        self.execution_command = max(self.execution_command - step,
                                     min(self.execution_command + step, target))
        return self.execution_command
