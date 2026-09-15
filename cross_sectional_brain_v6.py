"""Offline Version-6 cross-sectional swing-ranking experiment."""

from __future__ import annotations

import json
import math
import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, time
from pathlib import Path
from statistics import median

from brain_research_lab import Candle, load_candles_csv
from walk_forward_v5 import WINDOWS


@dataclass(frozen=True)
class RankingConfig:
    short_return_bars: int = 30
    long_return_bars: int = 120
    trend_bars: int = 50
    breakout_bars: int = 120
    volume_sessions: int = 20
    breakout_distance_minimum: float = -0.02
    holding_bars: int = 18
    maximum_candidates: int = 2
    slippage_bps_per_side: float = 5.0
    cost_bps_per_side: float = 5.0
    decision_hour: int = 14
    decision_minute: int = 15
    entry_pullback_bps: float = 0.0
    entry_valid_bars: int = 1


@dataclass(frozen=True)
class FeatureRow:
    symbol: str
    timestamp: object
    rs_short: float
    rs_long: float
    breakout_distance: float
    volume_ratio: float
    above_trend: bool


def percentile_ranks(values: dict[str, float]) -> dict[str, float]:
    """Return average-tie percentile ranks from 0 to 1."""
    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda item: item[1])
    denominator = max(1, len(ordered) - 1)
    result = {}
    index = 0
    while index < len(ordered):
        end = index
        while end + 1 < len(ordered) and ordered[end + 1][1] == ordered[index][1]:
            end += 1
        rank = ((index + end) / 2) / denominator
        for position in range(index, end + 1):
            result[ordered[position][0]] = rank
        index = end + 1
    return result


def feature_at(
    symbol: str, candles: list[Candle], index: int,
    market: list[Candle], market_index: int, config: RankingConfig,
) -> FeatureRow | None:
    warmup = max(config.long_return_bars, config.breakout_bars, config.trend_bars)
    if index < warmup or market_index < config.long_return_bars:
        return None
    candle = candles[index]
    if candle.timestamp != market[market_index].timestamp:
        return None
    decision_time = time(config.decision_hour, config.decision_minute)
    previous_final_volumes = [
        bar.volume for bar in candles[:index]
        if bar.timestamp.time().replace(tzinfo=None) == decision_time
    ][-config.volume_sessions:]
    if len(previous_final_volumes) < config.volume_sessions:
        return None
    reference_volume = median(previous_final_volumes)
    if reference_volume <= 0:
        return None
    market_short = market[market_index].close / market[market_index - config.short_return_bars].close - 1
    market_long = market[market_index].close / market[market_index - config.long_return_bars].close - 1
    stock_short = candle.close / candles[index - config.short_return_bars].close - 1
    stock_long = candle.close / candles[index - config.long_return_bars].close - 1
    prior_high = max(bar.high for bar in candles[index - config.breakout_bars:index])
    prior_trend = sum(bar.close for bar in candles[index - config.trend_bars:index]) / config.trend_bars
    return FeatureRow(
        symbol=symbol, timestamp=candle.timestamp,
        rs_short=stock_short - market_short,
        rs_long=stock_long - market_long,
        breakout_distance=candle.close / prior_high - 1,
        volume_ratio=candle.volume / reference_volume,
        above_trend=candle.close > prior_trend,
    )


def forward_outcome(candles: list[Candle], signal_index: int, config: RankingConfig) -> dict | None:
    entry_index = signal_index + 1
    if config.entry_pullback_bps:
        limit_price = candles[signal_index].close * (1 - config.entry_pullback_bps / 10_000)
        last_entry_index = min(signal_index + config.entry_valid_bars, len(candles) - 1)
        fill_index = None
        fill_raw = None
        for index in range(entry_index, last_entry_index + 1):
            if candles[index].low <= limit_price:
                fill_index = index
                fill_raw = min(limit_price, candles[index].open)
                break
        if fill_index is None:
            return None
        entry_index = fill_index
        entry = min(limit_price, fill_raw * (1 + config.slippage_bps_per_side / 10_000))
    else:
        entry = candles[entry_index].open * (1 + config.slippage_bps_per_side / 10_000)
    exit_index = entry_index + config.holding_bars - 1
    if exit_index >= len(candles):
        return None
    exit_price = candles[exit_index].close * (1 - config.slippage_bps_per_side / 10_000)
    fees = (entry + exit_price) * config.cost_bps_per_side / 10_000
    return {"net_return": (exit_price - entry - fees) / entry,
            "entry_index": entry_index, "exit_index": exit_index, "entry_price": entry}


def net_forward_return(candles: list[Candle], signal_index: int, config: RankingConfig) -> float | None:
    outcome = forward_outcome(candles, signal_index, config)
    return outcome["net_return"] if outcome else None


def summary(values: list[float]) -> dict:
    return {
        "observations": len(values),
        "mean_net_return": sum(values) / len(values) if values else None,
        "win_rate": sum(value > 0 for value in values) / len(values) if values else None,
        "total_net_return": sum(values),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision-time", default="14:15")
    parser.add_argument("--version", default="V6_CROSS_SECTIONAL")
    parser.add_argument("--output-dir", default="brain_results_v6")
    parser.add_argument("--output-name", default="cross_sectional_v6.json")
    parser.add_argument("--entry-pullback-bps", type=float, default=0.0)
    parser.add_argument("--entry-valid-bars", type=int, default=1)
    args = parser.parse_args()
    hour, minute = (int(part) for part in args.decision_time.split(":"))
    config = RankingConfig(decision_hour=hour, decision_minute=minute,
                           entry_pullback_bps=args.entry_pullback_bps,
                           entry_valid_bars=args.entry_valid_bars)
    decision_time = time(config.decision_hour, config.decision_minute)
    paths = sorted(Path("historical_data_60minute").glob("NSE_*60minute*.csv"))
    paths += sorted(Path("historical_data_v5_additional_60minute").glob("NSE_*60minute*.csv"))
    if len(paths) != 20:
        raise RuntimeError(f"expected 20 frozen equities, got {len(paths)}")
    series = {path.name.split("_", 2)[1]: load_candles_csv(path, 100) for path in paths}
    market_path = next(Path("historical_data_market_60minute").glob("NSE_NIFTY*60minute*.csv"))
    market = load_candles_csv(market_path, 100)
    market_index = {bar.timestamp: index for index, bar in enumerate(market)}
    equity_indices = {
        symbol: {bar.timestamp: index for index, bar in enumerate(candles)}
        for symbol, candles in series.items()
    }

    selections = []
    unfilled_selections = 0
    no_candidate_days = 0
    decision_days = 0
    for market_i, market_bar in enumerate(market):
        if market_bar.timestamp.time().replace(tzinfo=None) != decision_time:
            continue
        if market_i < max(config.long_return_bars, config.trend_bars):
            continue
        decision_days += 1
        prior_market_mean = sum(
            bar.close for bar in market[market_i - config.trend_bars:market_i]
        ) / config.trend_bars
        if market_bar.close <= prior_market_mean:
            no_candidate_days += 1
            continue
        features = []
        for symbol, candles in series.items():
            equity_i = equity_indices[symbol].get(market_bar.timestamp)
            if equity_i is None:
                continue
            feature = feature_at(symbol, candles, equity_i, market, market_i, config)
            if feature is not None:
                features.append((feature, equity_i))
        ranks = {
            name: percentile_ranks({feature.symbol: getattr(feature, name) for feature, _ in features})
            for name in ("rs_short", "rs_long", "breakout_distance", "volume_ratio")
        }
        eligible = []
        for feature, equity_i in features:
            if not (feature.above_trend and feature.rs_long > 0
                    and feature.breakout_distance >= config.breakout_distance_minimum
                    and feature.volume_ratio >= 1):
                continue
            score = 100 * (
                .30 * ranks["rs_short"][feature.symbol]
                + .30 * ranks["rs_long"][feature.symbol]
                + .25 * ranks["breakout_distance"][feature.symbol]
                + .15 * ranks["volume_ratio"][feature.symbol]
            )
            eligible.append((score, feature.symbol, equity_i))
        eligible.sort(reverse=True)
        chosen = eligible[:config.maximum_candidates]
        if not chosen:
            no_candidate_days += 1
            continue
        eligible_outcomes = [
            net_forward_return(series[symbol], equity_i, config)
            for _, symbol, equity_i in eligible
        ]
        filled_universe = [value for value in eligible_outcomes if value is not None]
        universe_mean = sum(filled_universe) / len(filled_universe) if filled_universe else None
        for score, symbol, equity_i in chosen:
            outcome = forward_outcome(series[symbol], equity_i, config)
            if outcome is None:
                unfilled_selections += 1
                continue
            forward = outcome["net_return"]
            selections.append({
                "timestamp": market_bar.timestamp.isoformat(), "symbol": symbol,
                "score": score, "net_forward_return": forward,
                "eligible_universe_mean": universe_mean,
                "excess_return": forward - universe_mean if universe_mean is not None else None,
                "entry_delay_bars": outcome["entry_index"] - equity_i,
            })

    returns = [row["net_forward_return"] for row in selections]
    excess = [row["excess_return"] for row in selections if row["excess_return"] is not None]
    by_window = {}
    for start, end in WINDOWS:
        window_rows = [row for row in selections if start <= date.fromisoformat(row["timestamp"][:10]) < end]
        by_window[f"{start}_{end}"] = {
            **summary([row["net_forward_return"] for row in window_rows]),
            "mean_excess_return": (
                sum(row["excess_return"] for row in window_rows) / len(window_rows)
                if window_rows else None
            ),
        }
    profitable_windows = sum(value["total_net_return"] > 0 for value in by_window.values())
    report = {
        "research_version": args.version,
        "config": asdict(config), "decision_days": decision_days,
        "no_candidate_days": no_candidate_days,
        "unfilled_selections": unfilled_selections,
        "candidate_day_rate": (decision_days - no_candidate_days) / decision_days,
        "combined": {**summary(returns),
                     "mean_excess_return": sum(excess) / len(excess) if excess else None},
        "profitable_windows": profitable_windows,
        "total_windows": len(by_window), "by_window": by_window,
        "selection_counts": dict(Counter(row["symbol"] for row in selections)),
        "selections": selections,
    }
    output = Path(args.output_dir)
    output.mkdir(exist_ok=True)
    (output / args.output_name).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "decision_days", "no_candidate_days", "candidate_day_rate", "combined",
        "profitable_windows", "total_windows", "selection_counts", "unfilled_selections"
    )}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
