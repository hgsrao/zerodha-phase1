"""Pure paper-only 12-1 momentum variant for live observatory telemetry.

Originally kept in sync with what BRAIN_RESEARCH_SPEC_V11/V13/V14 actually
validated: the 19-symbol universe (POLYCAB excluded - see
extended_history_windows.py). Extended 2026-08-16 to a 50-symbol universe
(51-symbol SECTORS minus POLYCAB) - see p01d-and-v11-bridge-status memory
for the full record, including the real, honestly-preserved finding that
this expansion weakens V14's own 17-fold validated result (11/17 -> 3/17).

UNIVERSE_MODE (environment variable, read once at import time - each
separate process/terminal gets its own value, which is exactly what lets
"the earlier engine" and "the new engine" run side by side in two
different terminals without colliding): unset or "expanded" (default)
uses SECTORS (51 symbols, current); "original" uses SECTORS_ORIGINAL_20
(19 real symbols once POLYCAB is excluded) - the same universe every
validated V11/V13/V14 result was ever actually measured against.

Formation data drawn from the corrected extended download, ₹1,00,000
paper capital, and real Zerodha delivery costs applied to the paper entry
rather than a cost-free mark. This module still never imports a broker
SDK, the runner, or request_entry - it is read-only telemetry, not an
execution path.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from brain_research_lab import load_candles_csv
from extended_history_windows import EXCLUDED_SYMBOLS
from portfolio_brain_v9 import SECTORS, SECTORS_ORIGINAL_20
from zerodha_delivery_costs import buy_cost


UNIVERSE_MODE = os.environ.get("UNIVERSE_MODE", "expanded").strip().lower()
_ACTIVE_SECTORS = SECTORS_ORIGINAL_20 if UNIVERSE_MODE == "original" else SECTORS

EXTERNAL_UNIVERSE = tuple(sorted(set(_ACTIVE_SECTORS) - EXCLUDED_SYMBOLS))

# Prefer the corrected extended download (2016-2026, bad ticks fixed by
# prepare_research_ohlcv.py) when it exists on disk; fall back to the
# original 3-year raw download so this module still works in an environment
# that never ran the extended pull. Either way only the most recent ~13
# months of month-end closes are actually used by the 12-1 formation calc.
_EXTENDED_DIR = Path("historical_data_60minute_extended_ready")
_ORIGINAL_DIRS = (Path("historical_data_60minute"), Path("historical_data_v5_additional_60minute"))


def _formation_source_paths() -> list[Path]:
    if _EXTENDED_DIR.is_dir():
        paths = sorted(_EXTENDED_DIR.glob("NSE_*60minute*.csv"))
        if paths:
            return paths
    paths = []
    for directory in _ORIGINAL_DIRS:
        paths += sorted(directory.glob("NSE_*60minute*.csv"))
    return paths


@lru_cache(maxsize=1)
def formation_prices() -> tuple[str, str, dict[str, tuple[float, float]]]:
    month_closes: dict[str, dict[str, float]] = {}
    for path in _formation_source_paths():
        symbol = path.name.split("_", 2)[1]
        if symbol not in EXTERNAL_UNIVERSE:
            continue
        daily: dict[str, float] = {}
        for bar in load_candles_csv(path, 100):
            daily[bar.timestamp.date().isoformat()] = bar.close
        for day, close in daily.items():
            month_closes.setdefault(day[:7], {})[symbol] = close
    months = sorted(month_closes)
    if len(months) < 13:
        raise RuntimeError("insufficient 12-1 formation history")
    recent_month, old_month = months[-2], months[-13]
    pairs = {
        symbol: (month_closes[old_month][symbol], month_closes[recent_month][symbol])
        for symbol in EXTERNAL_UNIVERSE
        if symbol in month_closes[old_month] and symbol in month_closes[recent_month]
    }
    return old_month, recent_month, pairs


def evaluate_external_momentum(
    quotes: Mapping[str, Mapping[str, Any]], *, paper_capital: float = 100_000.0,
    positions: int = 4, apply_real_costs: bool = True,
) -> dict[str, Any]:
    old_month, recent_month, pairs = formation_prices()
    scores = {symbol: recent / old - 1 for symbol, (old, recent) in pairs.items() if old > 0}
    selected = sorted(scores, key=scores.get, reverse=True)[:positions]
    allocation = paper_capital / positions
    basket = []
    marked_value = 0.0
    cash = paper_capital
    for symbol in selected:
        payload = quotes.get(f"NSE:{symbol}", {})
        try:
            price = float(payload.get("last_price"))
        except (TypeError, ValueError):
            price = 0.0
        reference = pairs[symbol][1]
        quantity = int(allocation // reference) if reference > 0 else 0
        invested = quantity * reference
        entry_cost = buy_cost(invested).total if apply_real_costs and invested > 0 else 0.0
        current_value = quantity * price if price > 0 else None
        cash -= invested + entry_cost
        if current_value is not None:
            marked_value += current_value
        basket.append({"symbol": symbol, "momentum_12_1": scores[symbol],
                       "formation_price": reference, "live_price": price or None,
                       "paper_quantity": quantity, "paper_value": current_value,
                       "entry_cost": entry_cost})
    complete = all(row["live_price"] is not None for row in basket)
    equity = cash + marked_value if complete else None
    return {
        "variant_id": "EXTERNAL_CROSS_SECTIONAL_12_1",
        "mode": "PAPER_ONLY_NO_EXECUTION_PATH",
        "formation_period": f"{old_month} to {recent_month}",
        "rebalance_frequency": "MONTHLY",
        "paper_starting_capital": paper_capital,
        "real_costs_applied": bool(apply_real_costs),
        "selected": basket,
        "paper_cash": cash,
        "paper_equity": equity,
        "paper_return": equity / paper_capital - 1 if equity is not None else None,
        "status": "MARKED" if complete else "WAITING_FOR_ALL_QUOTES",
        "authoritative_strategy": False,
        "broker_write": False,
        "order_api": False,
    }
