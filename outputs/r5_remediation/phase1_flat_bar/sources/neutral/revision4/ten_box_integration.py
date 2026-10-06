"""Deterministic missing-box integrations for the sealed Revision 4 replay.

This module deliberately does *not* reuse ``revision4_production``: that
prototype contains random scoring and placeholder state.  These components
operate only on the bar available at the decision timestamp and on prior bar
history.  ``BoxAudit`` is part of the replay evidence: it makes a missing path
observable instead of silently treating a partial pipeline as ten-box.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, Mapping, Sequence, Tuple

import pandas as pd

from revision2.boxes import DataIngestionBox, L2DataCertifierBox, UnifiedExecutionBox
from revision4.contracts import Bar, EffectiveConfig, ExitEvent, ExitReason, PortfolioSnapshot, Position


@dataclass(frozen=True)
class ChartSignal:
    momentum: float
    rsi: float
    vwap: float
    confirms_long: bool
    confirms_short: bool


class BoxAudit:
    """Counts real calls and explicit rejections for each named box."""

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()
        self.rejections: Counter[Tuple[str, str]] = Counter()

    def called(self, name: str) -> None:
        self.calls[name] += 1

    def rejected(self, name: str, reason: str) -> None:
        self.rejections[(name, reason)] += 1

    def report(self) -> dict:
        return {
            "calls": dict(sorted(self.calls.items())),
            "rejections": {
                f"{box}:{reason}": count
                for (box, reason), count in sorted(self.rejections.items())
            },
        }


class TenBoxIntegration:
    """The six non-PA/ID/MPC/PM paths required by the Revision 4 contract.

    Revision 2 remains the authoritative implementation of PA, ID, MPC and
    position sizing.  This class supplies data admission/certification, chart
    confirmation, execution-window validation, deterministic grid context,
    a dedicated risk check, exit control, and closed-trade performance records.
    """

    DATA = "data_input"
    CHART = "chart_studies"
    ENTRY = "entry_validator"
    RISK = "risk_manager"
    GRID = "grid_sync"
    EXIT = "exit_controller"
    PERFORMANCE = "performance_tracker"

    def __init__(self, config: EffectiveConfig) -> None:
        self.config = config
        self.audit = BoxAudit()
        self._ingestion = DataIngestionBox()
        self._certifier = L2DataCertifierBox()
        self._execution = UnifiedExecutionBox()
        self._peak_equity = 0.0
        self._daily_pnl: Dict[str, float] = defaultdict(float)
        self._closed_trades = 0

    def admit_and_certify(self, symbol: str, history: pd.DataFrame) -> tuple[bool, str]:
        self.audit.called(self.DATA)
        admitted, reason, _trace = self._ingestion.admit(symbol, self.config)
        if not admitted:
            self.audit.rejected(self.DATA, reason)
            return False, reason
        certified, reason, _trace = self._certifier.certify(history, self.config)
        if not certified:
            self.audit.rejected(self.DATA, reason)
            return False, reason
        return True, "admitted_and_certified"

    def chart_signal(self, history: pd.DataFrame) -> ChartSignal:
        self.audit.called(self.CHART)
        closes = history["close"].astype(float)
        volumes = history["volume"].astype(float)
        period = max(2, int(self.config.require("momentum_calculation_period")))
        recent = closes.tail(period)
        momentum = float((recent.iloc[-1] / recent.iloc[0]) - 1.0) if len(recent) >= 2 else 0.0
        deltas = closes.diff().tail(14)
        gains = deltas.clip(lower=0).mean()
        losses = (-deltas.clip(upper=0)).mean()
        rsi = 50.0 if losses == 0 and gains == 0 else (100.0 if losses == 0 else float(100 - 100 / (1 + gains / losses)))
        cumulative_volume = volumes.cumsum()
        vwap = float((closes * volumes).cumsum().iloc[-1] / cumulative_volume.iloc[-1]) if cumulative_volume.iloc[-1] else float(closes.iloc[-1])
        close = float(closes.iloc[-1])
        return ChartSignal(
            momentum=momentum, rsi=rsi, vwap=vwap,
            confirms_long=momentum > 0.0 and close >= vwap and rsi >= 50.0,
            confirms_short=momentum < 0.0 and close <= vwap and rsi <= 50.0,
        )

    def validate_entry(self, direction: int, timestamp: str, chart: ChartSignal) -> tuple[bool, str]:
        self.audit.called(self.ENTRY)
        self.audit.called("execution_window")
        in_window, _bias, _trace = self._execution.check_window(timestamp, self.config)
        if not in_window:
            reason = "outside canonical execution window"
            self.audit.rejected(self.ENTRY, reason)
            return False, reason
        confirmed = chart.confirms_long if direction == 1 else chart.confirms_short
        if not confirmed:
            reason = "chart does not confirm PA direction"
            self.audit.rejected(self.ENTRY, reason)
            return False, reason
        return True, "entry_validated"

    def grid_allows(self, direction: int, bars: Mapping[str, Bar]) -> tuple[bool, str]:
        """Use contemporaneous universe breadth; no future or external index data."""
        self.audit.called(self.GRID)
        signed_returns = [
            (bar.close - bar.open) / bar.open for bar in bars.values() if bar.open > 0
        ]
        breadth = sum(signed_returns) / len(signed_returns) if signed_returns else 0.0
        if breadth and (breadth > 0) != (direction > 0):
            reason = "universe breadth opposes entry direction"
            self.audit.rejected(self.GRID, reason)
            return False, reason
        return True, "grid_aligned"

    def approve_risk(self, entry_price: float, stop_price: float, quantity: int) -> tuple[bool, str]:
        self.audit.called(self.RISK)
        risk = abs(entry_price - stop_price) * quantity
        cap = float(self.config.require("max_loss_per_trade_rupees"))
        if risk > cap:
            reason = f"risk Rs.{risk:.2f} exceeds cap Rs.{cap:.2f}"
            self.audit.rejected(self.RISK, reason)
            return False, reason
        return True, "risk_approved"

    def decide_exit(self, snapshot: PortfolioSnapshot, position: Position, bar: Bar, event_index: int,
                    atr: float, grid_exit_urgency: bool = False) -> tuple[ExitReason | None, float | None]:
        self.audit.called(self.EXIT)
        held = position.bars_held(event_index)
        max_hold = int(self.config.require("max_hold_bars"))
        min_hold = int(self.config.require("min_hold_bars"))
        if held >= max_hold:
            return ExitReason.TIME_EXIT, bar.close
        if position.direction == 1:
            if bar.low <= position.stop_price:
                return ExitReason.STOP_HIT, position.stop_price
            if held >= min_hold and bar.high >= position.target_price:
                return ExitReason.TARGET_HIT, position.target_price
        else:
            if bar.high >= position.stop_price:
                return ExitReason.STOP_HIT, position.stop_price
            if held >= min_hold and bar.low <= position.target_price:
                return ExitReason.TARGET_HIT, position.target_price
        # A hostile grid only tightens an already-established trailing exit.
        if grid_exit_urgency and held >= min_hold and atr > 0:
            trail = position.entry_price - atr * float(self.config.require("trailing_stop_atr_mult"))
            if position.direction == 1 and bar.close <= trail:
                return ExitReason.TIME_EXIT, bar.close
        return None, None

    def record_exit(self, event: ExitEvent) -> None:
        self.audit.called(self.PERFORMANCE)
        self._closed_trades += 1
        self._daily_pnl[pd.Timestamp(event.timestamp_exit).date().isoformat()] += event.pnl_realized

    def performance_report(self) -> dict:
        return {"closed_trades": self._closed_trades, "daily_pnl": dict(sorted(self._daily_pnl.items()))}
