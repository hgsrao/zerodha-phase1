"""Broker-free comparison of V9 with published momentum/trend controls.

This module only reads local CSV files.  It has no broker SDK, network, or order path.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from brain_research_lab import Candle, load_candles_csv
from portfolio_brain_v9 import PortfolioConfig, SECTORS, simulate


@dataclass(frozen=True)
class ComparisonConfig:
    starting_capital: float = 600_000.0
    positions: int = 4
    formation_months: int = 12
    skip_months: int = 1
    cost_bps_per_side: float = 5.0
    slippage_bps_per_side: float = 5.0


def daily_closes(candles: list[Candle]) -> dict[str, float]:
    result: dict[str, float] = {}
    for candle in candles:
        result[candle.timestamp.date().isoformat()] = candle.close
    return result


def month_ends(dates: list[str]) -> list[str]:
    ends: list[str] = []
    for index, value in enumerate(dates):
        if index == len(dates) - 1 or dates[index + 1][:7] != value[:7]:
            ends.append(value)
    return ends


def _prior_month_date(months: list[str], position: int, offset: int) -> str | None:
    target = position - offset
    return months[target] if target >= 0 else None


def _performance(curve: list[dict], start: float, turnover: float) -> dict:
    peak = start
    drawdown = 0.0
    for row in curve:
        peak = max(peak, row["equity"])
        if peak:
            drawdown = max(drawdown, (peak - row["equity"]) / peak)
    final = curve[-1]["equity"] if curve else start
    return {"final_equity": final, "net_return": final / start - 1,
            "maximum_drawdown": drawdown, "turnover": turnover}


def monthly_long_only(name: str, closes: dict[str, dict[str, float]], cfg: ComparisonConfig,
                      require_positive: bool, decision_window: tuple | None = None,
                      cost_model=None) -> dict:
    """Run the monthly 12-1 momentum portfolio.

    decision_window, if given, is a (start_date, end_date) pair (end exclusive,
    date objects). The day-by-day loop - and therefore every rebalance and
    every equity mark - is restricted to that window; formation scores still
    look back through the full closes history (as they must, to be able to
    rebalance at all near a window's start), but nothing outside the window is
    ever held or valued. This lets a caller run an isolated, fresh-capital
    backtest over just a training slice or just a test slice, exactly as
    portfolio_brain_v9.simulate()'s decision_window does for V9.

    cost_model, if given, must expose buy_cost(value) and sell_cost(value)
    returning objects with a .total in rupees (see zerodha_delivery_costs.py)
    - the real statutory fee schedule instead of cfg.cost_bps_per_side's flat
    guess. cfg.slippage_bps_per_side still applies on top either way; slippage
    is a modeling assumption regardless of how precisely the statutory costs
    are known, so it is not something a real fee schedule can replace. When
    cost_model is None, behavior is byte-for-byte identical to before this
    parameter existed.
    """
    common_dates = sorted(set.intersection(*(set(values) for values in closes.values())))
    ends = month_ends(common_dates)
    warmup = cfg.formation_months + cfg.skip_months
    evaluation_start = ends[warmup]
    dates = [value for value in common_dates if value >= evaluation_start]
    if decision_window is not None:
        window_start, window_end = decision_window[0].isoformat(), decision_window[1].isoformat()
        dates = [value for value in dates if window_start <= value < window_end]
    cash = cfg.starting_capital
    quantities = {symbol: 0 for symbol in closes}
    curve: list[dict] = []
    turnover = 0.0
    rebalance_dates: list[str] = []
    friction = (cfg.cost_bps_per_side + cfg.slippage_bps_per_side) / 10_000
    slippage_only = cfg.slippage_bps_per_side / 10_000

    for day in dates:
        if day in ends:
            month_i = ends.index(day)
            recent = _prior_month_date(ends, month_i, cfg.skip_months)
            old = _prior_month_date(ends, month_i, warmup)
            if recent and old:
                scores = {symbol: closes[symbol][recent] / closes[symbol][old] - 1
                          for symbol in closes}
                ranked = sorted(scores, key=scores.get, reverse=True)
                selected = [symbol for symbol in ranked
                            if not require_positive or scores[symbol] > 0][:cfg.positions]
                equity = cash + sum(quantities[s] * closes[s][day] for s in closes)
                # Sell the old paper portfolio, then buy the newly selected equal-weight basket.
                for symbol, quantity in quantities.items():
                    if quantity:
                        gross = quantity * closes[symbol][day]
                        if cost_model is not None:
                            gross_after_slippage = gross * (1 - slippage_only)
                            proceeds = gross_after_slippage - cost_model.sell_cost(gross_after_slippage).total
                        else:
                            proceeds = gross * (1 - friction)
                        cash += proceeds
                        turnover += gross
                        quantities[symbol] = 0
                allocation = equity / cfg.positions if selected else 0.0
                for symbol in selected:
                    raw_price = closes[symbol][day]
                    if cost_model is not None:
                        price_with_slippage = raw_price * (1 + slippage_only)
                    else:
                        price_with_slippage = raw_price * (1 + friction)
                    quantity = int(allocation // price_with_slippage)
                    if quantity <= 0:
                        continue
                    notional = quantity * price_with_slippage
                    total_cost = notional + cost_model.buy_cost(notional).total if cost_model is not None else notional
                    if total_cost <= cash:
                        quantities[symbol] = quantity
                        cash -= total_cost
                        turnover += quantity * closes[symbol][day]
                rebalance_dates.append(day)
        equity = cash + sum(quantities[s] * closes[s][day] for s in closes)
        curve.append({"timestamp": day, "equity": equity})
    return {"name": name, "evaluation_start": dates[0] if dates else evaluation_start,
            "decision_window": (
                [decision_window[0].isoformat(), decision_window[1].isoformat()]
                if decision_window is not None else None
            ),
            "method": ("12-month absolute trend; hold positive-trend assets only"
                       if require_positive else "Jegadeesh-Titman-style 12-1 cross-sectional winners"),
            "metrics": _performance(curve, cfg.starting_capital, turnover),
            "rebalances": len(rebalance_dates), "curve": curve}


def buy_hold(name: str, closes: dict[str, float], start_date: str,
             cfg: ComparisonConfig) -> dict:
    dates = [value for value in sorted(closes) if value >= start_date]
    friction = (cfg.cost_bps_per_side + cfg.slippage_bps_per_side) / 10_000
    entry = closes[dates[0]] * (1 + friction)
    quantity = int(cfg.starting_capital // entry)
    cash = cfg.starting_capital - quantity * entry
    curve = [{"timestamp": day, "equity": cash + quantity * closes[day]} for day in dates]
    return {"name": name, "evaluation_start": dates[0], "method": "buy and hold",
            "metrics": _performance(curve, cfg.starting_capital, quantity * closes[dates[0]]),
            "curve": curve}


def load_universe() -> tuple[dict[str, list[Candle]], list[Candle]]:
    paths = sorted(Path("historical_data_60minute").glob("NSE_*60minute*.csv"))
    paths += sorted(Path("historical_data_v5_additional_60minute").glob("NSE_*60minute*.csv"))
    series = {path.name.split("_", 2)[1]: load_candles_csv(path, 100) for path in paths}
    if set(series) != set(SECTORS):
        raise RuntimeError("frozen sector map and comparison universe differ")
    market_path = next(Path("historical_data_market_60minute").glob("NSE_NIFTY*60minute*.csv"))
    return series, load_candles_csv(market_path, 100)


def run_comparison(cfg: ComparisonConfig | None = None) -> dict:
    config = cfg or ComparisonConfig()
    series, market = load_universe()
    closes = {symbol: daily_closes(bars) for symbol, bars in series.items()}
    cross = monthly_long_only("EXTERNAL_CROSS_SECTIONAL_12_1", closes, config, False)
    trend = monthly_long_only("EXTERNAL_TIME_SERIES_12M", closes, config, True)
    start = cross["evaluation_start"]
    portfolio_cfg = PortfolioConfig(starting_capital=config.starting_capital)
    v9 = simulate("OUR_V9_TOP4_SECTOR", 4, 4, True, series, market, portfolio_cfg)
    v9_curve = [row for row in v9["curve"] if row["timestamp"][:10] >= start]
    if v9_curve:
        base = v9_curve[0]["equity"]
        normalized = [{"timestamp": row["timestamp"],
                       "equity": config.starting_capital * row["equity"] / base}
                      for row in v9_curve]
        common_turnover = sum(
            trade["entry_notional"] + trade["exit_notional"]
            for trade in v9["trades"] if trade["entry_time"][:10] >= start
        )
        v9_result = {"name": "OUR_V9_TOP4_SECTOR", "evaluation_start": start,
                     "method": "V9 hourly ranking, patient entry, sector/risk controls",
                     "metrics": _performance(normalized, config.starting_capital,
                                              common_turnover), "curve": normalized}
    else:
        raise RuntimeError("V9 curve does not cover the common evaluation period")
    nifty = buy_hold("NIFTY_BUY_HOLD", daily_closes(market), start, config)
    result = {
        "research_version": "MULTI_BRAIN_EXTERNAL_COMPARISON_V1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "safety": {"live_trading_enabled": False, "broker_calls": 0,
                   "production_runner_started": False},
        "config": asdict(config),
        "limitations": [
            "Only 20 current equities and roughly three years of data; survivorship bias is present.",
            "Published models were adapted to long-only cash equities; this is not an exact replication of long-short research portfolios.",
            "The comparison period begins after the 12-month formation warm-up and is too short for a production decision.",
        ],
        "models": [v9_result, cross, trend, nifty],
    }
    return result


def main() -> int:
    result = run_comparison()
    output = Path("brain_results_external")
    output.mkdir(exist_ok=True)
    (output / "external_model_comparison.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    compact = {model["name"]: model["metrics"] for model in result["models"]}
    print(json.dumps(compact, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
