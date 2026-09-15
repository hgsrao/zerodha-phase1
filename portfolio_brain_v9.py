"""Offline event-driven Version-9 portfolio simulator. No broker interfaces."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import time
from pathlib import Path

from brain_research_lab import Candle, load_candles_csv
from cross_sectional_brain_v6 import RankingConfig, feature_at, percentile_ranks
from walk_forward_v5 import WINDOWS


SECTORS = {
    "AXISBANK": "BANK", "HDFCBANK": "BANK", "ICICIBANK": "BANK",
    "KOTAKBANK": "BANK", "SBIN": "BANK", "BAJFINANCE": "FINANCE",
    "BHARTIARTL": "TELECOM", "HINDUNILVR": "CONSUMER", "ITC": "CONSUMER",
    "INFY": "TECH", "TCS": "TECH", "LAURUSLABS": "PHARMA",
    "SUNPHARMA": "PHARMA", "ZYDUSLIFE": "PHARMA", "LT": "INDUSTRIAL",
    "MARUTI": "AUTO", "NTPC": "POWER", "POLYCAB": "ELECTRICAL",
    "RELIANCE": "ENERGY", "TATASTEEL": "METALS",
    # 2026-08-16 addition: the remaining Nifty-48 research universe
    # (see v11_yahoo_broader_universe_backtest.py's own NIFTY_SYMBOLS
    # list for where this came from). Deliberately ADDS to the frozen-
    # quality original 20 above rather than replacing any of them - this
    # dict is read tolerantly by external_momentum_shadow.py (a symbol
    # with no 60-minute data file yet is just skipped from the momentum
    # ranking, not a hard failure), so extending it here is safe even
    # before the corresponding historical data has been backfilled.
    # portfolio_brain_v9.py's OWN standalone main() (V9's legacy backtest
    # entrypoint, not part of the live V11 bridge/runner path) enforces a
    # strict "SECTORS keys must exactly match available data files"
    # check - satisfied for all 51 symbols as of 2026-08-16 (real 60-
    # minute Zerodha data backfilled into both historical_data_60minute/
    # and historical_data_v5_additional_60minute/, matching what
    # external_model_comparison.py's own loader also needs - see
    # p01d-and-v11-bridge-status memory).
    "TITAN": "CONSUMER", "M&M": "AUTO", "ADANIENT": "INDUSTRIAL",
    "ADANIPORTS": "INDUSTRIAL", "HCLTECH": "TECH", "ULTRACEMCO": "CEMENT",
    "BAJAJFINSV": "FINANCE", "BAJAJ-AUTO": "AUTO", "JSWSTEEL": "METALS",
    "ETERNAL": "CONSUMER", "BEL": "DEFENSE", "ONGC": "ENERGY",
    "SHRIRAMFIN": "FINANCE", "ASIANPAINT": "CONSUMER", "COALINDIA": "MINING",
    "POWERGRID": "POWER", "HINDALCO": "METALS", "EICHERMOT": "AUTO",
    "GRASIM": "INDUSTRIAL", "INDIGO": "AVIATION", "WIPRO": "TECH",
    "SBILIFE": "INSURANCE", "JIOFIN": "FINANCE", "TECHM": "TECH",
    "TRENT": "CONSUMER", "APOLLOHOSP": "HEALTHCARE", "CIPLA": "PHARMA",
    "HDFCLIFE": "INSURANCE", "TATACONSUM": "CONSUMER", "DRREDDY": "PHARMA",
    "MAXHEALTH": "HEALTHCARE",
}

# The original, frozen-quality 20-symbol universe, preserved verbatim
# (not derived/filtered from SECTORS above) so "the earlier engine" stays
# a real, runnable, side-by-side option after 2026-08-16's expansion -
# not just a historical footnote. Used by external_momentum_shadow.py
# when UNIVERSE_MODE=original is set in the environment; SECTORS above
# (51 symbols) is what every default/unconfigured run still uses.
SECTORS_ORIGINAL_20 = {
    "AXISBANK": "BANK", "HDFCBANK": "BANK", "ICICIBANK": "BANK",
    "KOTAKBANK": "BANK", "SBIN": "BANK", "BAJFINANCE": "FINANCE",
    "BHARTIARTL": "TELECOM", "HINDUNILVR": "CONSUMER", "ITC": "CONSUMER",
    "INFY": "TECH", "TCS": "TECH", "LAURUSLABS": "PHARMA",
    "SUNPHARMA": "PHARMA", "ZYDUSLIFE": "PHARMA", "LT": "INDUSTRIAL",
    "MARUTI": "AUTO", "NTPC": "POWER", "POLYCAB": "ELECTRICAL",
    "RELIANCE": "ENERGY", "TATASTEEL": "METALS",
}


@dataclass(frozen=True)
class PortfolioConfig:
    starting_capital: float = 100_000.0
    risk_fraction: float = 0.0025
    maximum_open_risk_fraction: float = 0.01
    maximum_position_fraction: float = 0.20
    maximum_deployment_fraction: float = 0.80
    atr_window: int = 14
    atr_stop_multiple: float = 1.5
    pullback_bps: float = 25.0
    limit_valid_bars: int = 6
    holding_bars: int = 18
    slippage_bps_per_side: float = 5.0
    cost_bps_per_side: float = 5.0


@dataclass
class Pending:
    symbol: str
    sector: str
    score: float
    limit: float
    signal_atr: float
    expires_index: int


@dataclass
class Position:
    symbol: str
    sector: str
    quantity: int
    entry_price: float
    stop_price: float
    entry_fee: float
    entry_index: int
    exit_index: int


def true_range(candles: list[Candle], index: int) -> float:
    previous_close = candles[index - 1].close
    bar = candles[index]
    return max(bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close))


def atr(candles: list[Candle], index: int, window: int) -> float:
    return sum(true_range(candles, i) for i in range(index - window + 1, index + 1)) / window


def position_quantity(equity: float, available_cash: float, deployed: float,
                      entry: float, stop_distance: float, cfg: PortfolioConfig) -> int:
    if equity <= 0 or entry <= 0 or stop_distance <= 0:
        return 0
    by_risk = math.floor(equity * cfg.risk_fraction / stop_distance)
    by_position = math.floor(equity * cfg.maximum_position_fraction / entry)
    deployment_room = max(0.0, equity * cfg.maximum_deployment_fraction - deployed)
    by_deployment = math.floor(deployment_room / entry)
    by_cash = math.floor(available_cash / (entry * (1 + cfg.cost_bps_per_side / 10_000)))
    return max(0, min(by_risk, by_position, by_deployment, by_cash))


def metrics(trades: list[dict], curve: list[dict], starting_capital: float) -> dict:
    pnls = [trade["net_pnl"] for trade in trades]
    gross_profit = sum(value for value in pnls if value > 0)
    gross_loss = abs(sum(value for value in pnls if value < 0))
    peak = starting_capital
    maximum_drawdown = 0.0
    for row in curve:
        peak = max(peak, row["equity"])
        maximum_drawdown = max(maximum_drawdown, (peak - row["equity"]) / peak)
    final_equity = curve[-1]["equity"] if curve else starting_capital
    return {
        "final_equity": final_equity,
        "net_return": final_equity / starting_capital - 1,
        "maximum_drawdown": maximum_drawdown,
        "trades": len(trades),
        "win_rate": sum(value > 0 for value in pnls) / len(pnls) if pnls else None,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "turnover": sum(trade["entry_notional"] + trade["exit_notional"] for trade in trades),
        "average_capital_utilization": (
            sum(row["deployed"] / row["equity"] for row in curve if row["equity"] > 0) / len(curve)
            if curve else 0
        ),
    }


def simulate(name: str, maximum_positions: int, candidates_per_day: int,
             sector_control: bool, series: dict[str, list[Candle]],
             market: list[Candle], cfg: PortfolioConfig,
             decision_window: tuple | None = None) -> dict:
    """Run the V9 portfolio simulation.

    decision_window, if given, is a (start_date, end_date) pair (end exclusive).
    New candidates are only ever selected when the decision-hour bar's date falls
    inside that window; every other mechanic (feature lookback, fills, stops,
    time exits, equity marking) is untouched and still runs across the full
    series. This is what lets a caller reuse this exact, unmodified simulator to
    do anchored walk-forward evaluation: restrict new-entry timing to a training
    slice for variant selection, then to a disjoint test slice for out-of-sample
    evaluation, without ever duplicating the trading logic.
    """
    ranking = RankingConfig(decision_hour=13, decision_minute=15)
    equity_indices = {symbol: {bar.timestamp: i for i, bar in enumerate(bars)}
                      for symbol, bars in series.items()}
    cash = cfg.starting_capital
    positions: dict[str, Position] = {}
    pending: dict[str, Pending] = {}
    trades: list[dict] = []
    curve: list[dict] = []
    decision_time = time(13, 15)

    for market_i, market_bar in enumerate(market):
        timestamp = market_bar.timestamp
        closes = {symbol: bars[index].close for symbol, bars in series.items()
                  if (index := equity_indices[symbol].get(timestamp)) is not None}

        # Protective and time exits are processed before new fills.
        for symbol, position in list(positions.items()):
            index = equity_indices[symbol].get(timestamp)
            if index is None:
                continue
            bar = series[symbol][index]
            reason = None
            raw_exit = None
            if bar.low <= position.stop_price:
                reason, raw_exit = "STOP", position.stop_price
            elif index >= position.exit_index:
                reason, raw_exit = "TIME", bar.close
            if reason:
                exit_price = raw_exit * (1 - cfg.slippage_bps_per_side / 10_000)
                exit_notional = exit_price * position.quantity
                exit_fee = exit_notional * cfg.cost_bps_per_side / 10_000
                cash += exit_notional - exit_fee
                entry_notional = position.entry_price * position.quantity
                net_pnl = (exit_price - position.entry_price) * position.quantity - position.entry_fee - exit_fee
                trades.append({"symbol": symbol, "sector": position.sector,
                               "entry_time": series[symbol][position.entry_index].timestamp.isoformat(),
                               "exit_time": timestamp.isoformat(), "reason": reason,
                               "entry_notional": entry_notional, "exit_notional": exit_notional,
                               "net_pnl": net_pnl})
                del positions[symbol]

        marked_equity = cash + sum(closes.get(symbol, position.entry_price) * position.quantity
                                   for symbol, position in positions.items())
        deployed = sum(closes.get(symbol, position.entry_price) * position.quantity
                       for symbol, position in positions.items())

        for symbol, order in sorted(list(pending.items()), key=lambda item: item[1].score, reverse=True):
            index = equity_indices[symbol].get(timestamp)
            if index is None:
                continue
            if index > order.expires_index:
                del pending[symbol]
                continue
            bar = series[symbol][index]
            if bar.low > order.limit or len(positions) >= maximum_positions:
                continue
            open_risk = sum((position.entry_price - position.stop_price) * position.quantity
                            for position in positions.values())
            risk_room = marked_equity * cfg.maximum_open_risk_fraction - open_risk
            fill = min(order.limit, bar.open * (1 + cfg.slippage_bps_per_side / 10_000))
            stop_distance = cfg.atr_stop_multiple * order.signal_atr
            quantity = position_quantity(marked_equity, cash, deployed, fill, stop_distance, cfg)
            quantity = min(quantity, math.floor(max(0, risk_room) / stop_distance))
            if quantity <= 0:
                continue
            entry_notional = fill * quantity
            fee = entry_notional * cfg.cost_bps_per_side / 10_000
            cash -= entry_notional + fee
            position = Position(symbol, order.sector, quantity, fill, fill - stop_distance,
                                fee, index, index + cfg.holding_bars - 1)
            positions[symbol] = position
            deployed += entry_notional
            del pending[symbol]
            # Conservative same-candle stop after a limit fill.
            if bar.low <= position.stop_price:
                exit_price = position.stop_price * (1 - cfg.slippage_bps_per_side / 10_000)
                exit_notional = exit_price * quantity
                exit_fee = exit_notional * cfg.cost_bps_per_side / 10_000
                cash += exit_notional - exit_fee
                trades.append({"symbol": symbol, "sector": position.sector,
                               "entry_time": timestamp.isoformat(), "exit_time": timestamp.isoformat(),
                               "reason": "SAME_BAR_STOP", "entry_notional": entry_notional,
                               "exit_notional": exit_notional,
                               "net_pnl": (exit_price - fill) * quantity - fee - exit_fee})
                del positions[symbol]

        within_decision_window = (
            decision_window is None
            or decision_window[0] <= timestamp.date() < decision_window[1]
        )
        if (timestamp.time().replace(tzinfo=None) == decision_time
                and market_i >= max(ranking.long_return_bars, ranking.trend_bars)
                and within_decision_window):
            prior_market_mean = sum(bar.close for bar in market[market_i - ranking.trend_bars:market_i]) / ranking.trend_bars
            if market_bar.close > prior_market_mean:
                features = []
                for symbol, bars in series.items():
                    index = equity_indices[symbol].get(timestamp)
                    if index is None:
                        continue
                    feature = feature_at(symbol, bars, index, market, market_i, ranking)
                    if feature is not None:
                        features.append((feature, index))
                ranks = {field: percentile_ranks({feature.symbol: getattr(feature, field)
                                                  for feature, _ in features})
                         for field in ("rs_short", "rs_long", "breakout_distance", "volume_ratio")}
                eligible = []
                blocked_sectors = {position.sector for position in positions.values()}
                blocked_sectors |= {order.sector for order in pending.values()}
                for feature, index in features:
                    if feature.symbol in positions or feature.symbol in pending:
                        continue
                    if not (feature.above_trend and feature.rs_long > 0
                            and feature.breakout_distance >= ranking.breakout_distance_minimum
                            and feature.volume_ratio >= 1):
                        continue
                    sector = SECTORS[feature.symbol]
                    if sector_control and sector in blocked_sectors:
                        continue
                    score = 100 * (.30 * ranks["rs_short"][feature.symbol]
                                   + .30 * ranks["rs_long"][feature.symbol]
                                   + .25 * ranks["breakout_distance"][feature.symbol]
                                   + .15 * ranks["volume_ratio"][feature.symbol])
                    eligible.append((score, feature.symbol, sector, index))
                eligible.sort(reverse=True)
                slots = max(0, maximum_positions - len(positions) - len(pending))
                selected = []
                used_sectors = set(blocked_sectors)
                for item in eligible:
                    if len(selected) >= min(candidates_per_day, slots):
                        break
                    score, symbol, sector, index = item
                    if sector_control and sector in used_sectors:
                        continue
                    selected.append(item)
                    used_sectors.add(sector)
                for score, symbol, sector, index in selected:
                    bars = series[symbol]
                    signal_atr = atr(bars, index, cfg.atr_window)
                    pending[symbol] = Pending(symbol, sector, score,
                                              bars[index].close * (1 - cfg.pullback_bps / 10_000),
                                              signal_atr, index + cfg.limit_valid_bars)

        marked_equity = cash + sum(closes.get(symbol, position.entry_price) * position.quantity
                                   for symbol, position in positions.items())
        deployed = sum(closes.get(symbol, position.entry_price) * position.quantity
                       for symbol, position in positions.items())
        curve.append({"timestamp": timestamp.isoformat(), "equity": marked_equity, "deployed": deployed})

    result_metrics = metrics(trades, curve, cfg.starting_capital)
    window_returns = {}
    for start, end in WINDOWS:
        rows = [row for row in curve if start <= __import__("datetime").date.fromisoformat(row["timestamp"][:10]) < end]
        window_returns[f"{start}_{end}"] = rows[-1]["equity"] / rows[0]["equity"] - 1 if rows else None
    return {"name": name, "metrics": result_metrics, "window_returns": window_returns,
            "trades": trades, "curve": curve}


def main() -> int:
    paths = sorted(Path("historical_data_60minute").glob("NSE_*60minute*.csv"))
    paths += sorted(Path("historical_data_v5_additional_60minute").glob("NSE_*60minute*.csv"))
    series = {path.name.split("_", 2)[1]: load_candles_csv(path, 100) for path in paths}
    if set(series) != set(SECTORS):
        raise RuntimeError("frozen sector map and equity universe differ")
    market_path = next(Path("historical_data_market_60minute").glob("NSE_NIFTY*60minute*.csv"))
    market = load_candles_csv(market_path, 100)
    cfg = PortfolioConfig()
    variants = [
        simulate("TOP1", 1, 1, False, series, market, cfg),
        simulate("TOP2_SECTOR", 2, 2, True, series, market, cfg),
        simulate("TOP4_SECTOR", 4, 4, True, series, market, cfg),
    ]
    start_close, end_close = market[0].close, market[-1].close
    report = {"research_version": "V9_PORTFOLIO", "config": asdict(cfg),
              "nifty_buy_hold_return": end_close / start_close - 1,
              "variants": variants}
    output = Path("brain_results_v9")
    output.mkdir(exist_ok=True)
    (output / "portfolio_v9.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"nifty_buy_hold_return": report["nifty_buy_hold_return"],
                      "variants": [{"name": variant["name"], **variant["metrics"],
                                    "window_returns": variant["window_returns"]}
                                   for variant in variants]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
