"""Version 12: momentum selection with V9-style risk management.

Why this exists
----------------
The anchored walk-forward results so far pointed the same direction from two
angles:

- V9's own hand-tuned ranking (V10) has no demonstrated edge, but its
  execution discipline is sound: ATR-based stops, risk-fraction position
  sizing, sector caps, a hard cap on deployed capital.
- The published 12-1 cross-sectional momentum control (V11) walk-forward
  better than V9 (3 of 5 folds profitable vs. 2 of 5), but as implemented in
  external_model_comparison.py it is a pure monthly-rebalanced buy-and-hold
  basket - there is no stop-loss and no per-position risk budget at all. A
  single stock cratering 30% between rebalances is not protected against.

V12 is the natural combination: keep the momentum ranking that walk-forwarded
better, and wrap it in V9's risk machinery instead of V9's ranking. It reuses
portfolio_brain_v9's atr(), position_quantity(), metrics(), and SECTORS
unmodified, and external_model_comparison's month_ends() unmodified - no
separate reimplementation of either piece.

Mechanics
---------
Operates on DAILY bars (resampled from the existing hourly candles - no new
data required). Once a month, at each month-end date, rank all 20 symbols by
12-1 momentum (skip the most recent month, compare to 13 months back, exactly
as V11's EXTERNAL_CROSS_SECTIONAL_12_1 - the variant that won every fold with
enough training data). Select the top `maximum_positions`, one per sector if
sector_control is set. Liquidate any held position that fell out of the
target basket; for each new entry, size it by V9's risk-fraction formula
against a 14-day ATR stop, exactly as V9 does intraday.

Between rebalances, every held position is checked against its stop on every
trading day - if the daily low touches the stop, it is closed immediately
rather than waiting for the next scheduled rebalance. A stopped-out slot
stays in cash until the next rebalance; this deliberately does not chase or
average down.

This module makes no broker calls and is a research/validation artifact, not
a production strategy - it is not wired into request_entry() or any live
path.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from brain_research_lab import Candle, load_candles_csv
from external_model_comparison import month_ends
from portfolio_brain_v9 import PortfolioConfig, SECTORS, atr, metrics, position_quantity


FORMATION_MONTHS = 12
SKIP_MONTHS = 1


@dataclass
class Position:
    symbol: str
    sector: str
    quantity: int
    entry_price: float
    stop_price: float
    entry_fee: float
    entry_date: str


def resample_daily(candles: list[Candle]) -> list[Candle]:
    """One bar per calendar day from a same-symbol intraday candle series."""
    by_day: dict[date, list[Candle]] = defaultdict(list)
    for candle in candles:
        by_day[candle.timestamp.date()].append(candle)
    result = []
    for day in sorted(by_day):
        bars = sorted(by_day[day], key=lambda c: c.timestamp)
        result.append(Candle(
            timestamp=bars[0].timestamp, open=bars[0].open,
            high=max(c.high for c in bars), low=min(c.low for c in bars),
            close=bars[-1].close, volume=sum(c.volume for c in bars),
        ))
    return result


def simulate(
    name: str, maximum_positions: int, sector_control: bool,
    daily_series: dict[str, list[Candle]], cfg: PortfolioConfig,
    decision_window: tuple | None = None,
) -> dict:
    closes = {symbol: {c.timestamp.date().isoformat(): c.close for c in bars}
              for symbol, bars in daily_series.items()}
    bar_index = {symbol: {c.timestamp.date().isoformat(): i for i, c in enumerate(bars)}
                 for symbol, bars in daily_series.items()}
    common_dates = sorted(set.intersection(*(set(v) for v in closes.values())))
    ends = month_ends(common_dates)
    warmup = FORMATION_MONTHS + SKIP_MONTHS
    if len(ends) <= warmup:
        raise RuntimeError("insufficient daily history for the 12-1 formation warmup")
    evaluation_start = ends[warmup]
    dates = [value for value in common_dates if value >= evaluation_start]

    cash = cfg.starting_capital
    positions: dict[str, Position] = {}
    trades: list[dict] = []
    curve: list[dict] = []
    friction_in = 1 + cfg.slippage_bps_per_side / 10_000
    friction_out = 1 - cfg.slippage_bps_per_side / 10_000
    fee_rate = cfg.cost_bps_per_side / 10_000

    def within_window(day: str) -> bool:
        if decision_window is None:
            return True
        return decision_window[0] <= date.fromisoformat(day) < decision_window[1]

    for day in dates:
        # 1. Stop check, every held position, every day - never gated by
        #    decision_window. Protecting an existing position is not a new
        #    entry and must not depend on when the caller is allowed to pick
        #    new candidates.
        for symbol, position in list(positions.items()):
            index = bar_index[symbol].get(day)
            if index is None:
                continue
            bar = daily_series[symbol][index]
            if bar.low <= position.stop_price:
                exit_price = position.stop_price * friction_out
                exit_notional = exit_price * position.quantity
                exit_fee = exit_notional * fee_rate
                cash += exit_notional - exit_fee
                entry_notional = position.entry_price * position.quantity
                net_pnl = (exit_price - position.entry_price) * position.quantity - position.entry_fee - exit_fee
                trades.append({
                    "symbol": symbol, "sector": position.sector,
                    "entry_time": position.entry_date, "exit_time": day, "reason": "STOP",
                    "entry_notional": entry_notional, "exit_notional": exit_notional, "net_pnl": net_pnl,
                })
                del positions[symbol]

        # 2. Monthly rebalance, gated by decision_window.
        if day in ends and within_window(day):
            month_i = ends.index(day)
            recent_i = month_i - SKIP_MONTHS
            old_i = recent_i - FORMATION_MONTHS
            if recent_i >= 0 and old_i >= 0:
                recent, old = ends[recent_i], ends[old_i]
                scores = {}
                for symbol in closes:
                    if recent in closes[symbol] and old in closes[symbol] and closes[symbol][old] > 0:
                        scores[symbol] = closes[symbol][recent] / closes[symbol][old] - 1
                ranked = sorted(scores, key=scores.get, reverse=True)
                selected: list[str] = []
                used_sectors: set[str] = set()
                for symbol in ranked:
                    if len(selected) >= maximum_positions:
                        break
                    sector = SECTORS[symbol]
                    if sector_control and sector in used_sectors:
                        continue
                    selected.append(symbol)
                    used_sectors.add(sector)
                target = set(selected)

                for symbol in list(positions):
                    if symbol not in target:
                        position = positions[symbol]
                        exit_price = closes[symbol][day] * friction_out
                        exit_notional = exit_price * position.quantity
                        exit_fee = exit_notional * fee_rate
                        cash += exit_notional - exit_fee
                        entry_notional = position.entry_price * position.quantity
                        net_pnl = (exit_price - position.entry_price) * position.quantity - position.entry_fee - exit_fee
                        trades.append({
                            "symbol": symbol, "sector": position.sector,
                            "entry_time": position.entry_date, "exit_time": day, "reason": "REBALANCE_EXIT",
                            "entry_notional": entry_notional, "exit_notional": exit_notional, "net_pnl": net_pnl,
                        })
                        del positions[symbol]

                equity = cash + sum(positions[s].quantity * closes[s][day] for s in positions)
                deployed = sum(positions[s].quantity * closes[s][day] for s in positions)
                for symbol in selected:
                    if symbol in positions:
                        continue
                    index = bar_index[symbol][day]
                    if index < cfg.atr_window:
                        continue
                    signal_atr = atr(daily_series[symbol], index, cfg.atr_window)
                    if signal_atr <= 0:
                        continue
                    entry_price = closes[symbol][day] * friction_in
                    stop_distance = cfg.atr_stop_multiple * signal_atr
                    quantity = position_quantity(equity, cash, deployed, entry_price, stop_distance, cfg)
                    if quantity <= 0:
                        continue
                    entry_notional = entry_price * quantity
                    fee = entry_notional * fee_rate
                    if entry_notional + fee > cash:
                        continue
                    cash -= entry_notional + fee
                    deployed += entry_notional
                    positions[symbol] = Position(
                        symbol, SECTORS[symbol], quantity, entry_price,
                        entry_price - stop_distance, fee, day,
                    )

        equity_mark = cash + sum(
            positions[s].quantity * closes[s].get(day, positions[s].entry_price) for s in positions
        )
        deployed_mark = sum(
            positions[s].quantity * closes[s].get(day, positions[s].entry_price) for s in positions
        )
        curve.append({"timestamp": day, "equity": equity_mark, "deployed": deployed_mark})

    result_metrics = metrics(trades, curve, cfg.starting_capital)
    return {"name": name, "metrics": result_metrics, "trades": trades, "curve": curve}


def load_daily_universe() -> dict[str, list[Candle]]:
    paths = sorted(Path("historical_data_60minute").glob("NSE_*60minute*.csv"))
    paths += sorted(Path("historical_data_v5_additional_60minute").glob("NSE_*60minute*.csv"))
    hourly = {path.name.split("_", 2)[1]: load_candles_csv(path, 100) for path in paths}
    if set(hourly) != set(SECTORS):
        raise RuntimeError("frozen sector map and equity universe differ")
    return {symbol: resample_daily(bars) for symbol, bars in hourly.items()}


def main() -> int:
    daily_series = load_daily_universe()
    cfg = PortfolioConfig(starting_capital=100_000.0)
    variants = [
        simulate("V12_TOP4_SECTOR", 4, True, daily_series, cfg),
        simulate("V12_TOP2_SECTOR", 2, True, daily_series, cfg),
    ]
    report = {
        "research_version": "V12_MOMENTUM_RISK_MANAGED",
        "config": asdict(cfg),
        "variants": variants,
    }
    output = Path("brain_results_v12")
    output.mkdir(exist_ok=True)
    (output / "momentum_risk_managed_v12.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({v["name"]: v["metrics"] for v in variants}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
