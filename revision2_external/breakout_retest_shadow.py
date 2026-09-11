"""Fixed causal breakout-pullback/retest alpha, shadow-only.

The rejected continuation rule entered on the breakout bar.  This separate,
predeclared hypothesis waits for a later completed bar to retest the breakout
level and close back through it in the breakout direction.  It is designed to
test immediate adverse-selection, not to tune the rejected rule.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from revision2_external.study_entry_shadow import StudyEntryShadowLedger


@dataclass
class _Breakout:
    side: str
    level: float
    expiry_index: int


class BreakoutRetestShadow:
    """20-bar breakout, then a 1–5 bar causal retest/rejection confirmation."""
    def __init__(self, ledger: StudyEntryShadowLedger, retest_bars: int = 5) -> None:
        self.ledger = ledger
        self.retest_bars = int(retest_bars)
        self._waiting: Dict[str, List[_Breakout]] = {}

    def observe_precomputed(self, symbol: str, index: int, timestamp: object, bar: Any, atr: float, metrics: Dict[str, float]) -> None:
        close = float(metrics["close"]); high = float(bar["high"]); low = float(bar["low"])
        waiting = [x for x in self._waiting.get(symbol, []) if index <= x.expiry_index]
        self._waiting[symbol] = waiting
        bar_range = max(high - low, 1e-12)
        close_location = (close - low) / bar_range

        # Confirm retest first: this bar is entirely closed before entry is
        # scheduled, and the shared ledger fills only the next bar.
        for setup in list(waiting):
            confirmed = (
                setup.side == "BUY" and low <= setup.level and close >= setup.level and close_location >= .60
            ) or (
                setup.side == "SELL" and high >= setup.level and close <= setup.level and close_location <= .40
            )
            if not confirmed:
                continue
            scale = max(float(atr), 1e-6)
            extreme = min(low, setup.level) if setup.side == "BUY" else max(high, setup.level)
            obs = {
                "timestamp": str(timestamp), "symbol": symbol, "index": index,
                "alpha": "breakout_retest_v1", "side": setup.side,
                "close": close, "breakout_level": setup.level,
                "close_location": close_location,
                "confirmation": "retest_then_directional_close",
            }
            self.ledger.observations.append(obs)
            self.ledger.schedule(symbol=symbol, index=index, side=setup.side, setup_extreme=extreme, atr=scale, observation=obs)
            waiting.remove(setup)

        prior_high = float(metrics["prior_high"]); prior_low = float(metrics["prior_low"])
        vwap = float(metrics["session_vwap"]); ema = float(metrics["ema20"]); ema_lag = float(metrics["ema20_lag4"]); volume_ratio = float(metrics["volume_ratio"])
        if close > prior_high and close > vwap and ema > ema_lag and volume_ratio >= 1.2:
            waiting.append(_Breakout("BUY", prior_high, index + self.retest_bars))
        elif close < prior_low and close < vwap and ema < ema_lag and volume_ratio >= 1.2:
            waiting.append(_Breakout("SELL", prior_low, index + self.retest_bars))
