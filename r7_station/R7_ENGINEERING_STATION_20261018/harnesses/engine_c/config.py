"""Engine C execution configuration, declared independently of Engines A and B.

The values below are Engine C's own venue model.  The default commission schedule and slippage
band are numerically the same as the R5 paper model (``revision2.transaction_costs.leg_cost`` and
``slippage_cost_multiplier * mpc_base_slippage_fraction``) so that cross-engine comparisons are
like-for-like, but Engine C never calls that code to charge a fill: Backtrader charges commission
through ``NSEEquityIntradayCommission`` and applies slippage through its own broker.  The
reconciliation layer then checks Backtrader's native charges against the R5 cost model.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Dict, Optional

EXCHANGE_TZ = "Asia/Kolkata"
STARTING_CAPITAL = 1_000_000.0


@dataclass(frozen=True)
class CommissionSchedule:
    """NSE intraday cash-equity charges per executed fill (Zerodha-style, INR)."""
    brokerage_rate: float = 0.0003          # 0.03% of turnover ...
    brokerage_cap: float = 20.0             # ... capped at Rs 20 per executed fill
    exchange_txn_rate: float = 0.0000345    # exchange transaction + regulatory charges
    stt_sell_rate: float = 0.00025          # securities transaction tax, sell side only


@dataclass(frozen=True)
class EngineCConfig:
    starting_cash: float = STARTING_CAPITAL
    commission: CommissionSchedule = field(default_factory=CommissionSchedule)
    # Backtrader BackBroker.set_slippage_perc: adverse fraction applied to every market fill
    # (BUY pays up, SELL receives less), capped at the fill bar's high/low (slip_match).
    slippage_perc: float = 0.0005
    # None: Backtrader fills the whole order at the next bar's open (no volume model).
    # A number: Backtrader's native FixedBarPerc filler -- at most this % of the fill bar's
    # volume per bar; the remainder of a market order stays working at later bars.
    volume_fill_perc: Optional[float] = None
    # Venue rule enforced by NSEBackBroker: orders above this quantity are REJECTED natively.
    max_order_quantity: Dict[str, int] = field(default_factory=dict)
    # Entry orders that fill only partially are worked once (IOC semantics): the unfilled
    # remainder is cancelled through Backtrader's cancel() and R5 books the filled quantity.
    entry_time_in_force: str = "IOC_AFTER_FIRST_EXECUTION"
    fill_timing: str = "NEXT_BAR_OPEN"      # the only mode offered; see ACCEPTANCE_REPORT.md

    def as_dict(self) -> Dict[str, object]:
        return asdict(self)

    def config_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.as_dict(), sort_keys=True).encode()).hexdigest()
