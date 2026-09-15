"""Offline, broker-free research engine for the Version 1 strategy hypothesis."""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class StrategyConfig:
    slow_window: int = 50
    slow_slope_lookback: int = 0
    market_regime_window: int = 0
    breakout_window: int = 20
    atr_window: int = 14
    volume_window: int = 20
    volume_multiplier: float = 1.2
    atr_stop_multiple: float = 1.5
    reward_risk: float = 2.0
    max_holding_bars: int = 20
    quantity: int = 1
    cost_bps_per_side: float = 5.0
    slippage_bps_per_side: float = 5.0
    minimum_bars: int = 100

    def validate(self) -> None:
        integer_fields = (
            self.slow_window, self.breakout_window, self.atr_window,
            self.volume_window, self.max_holding_bars, self.quantity,
            self.minimum_bars,
        )
        if any(value <= 0 for value in integer_fields):
            raise ValueError("all window, holding, quantity, and minimum values must be positive")
        if self.slow_slope_lookback < 0:
            raise ValueError("slow_slope_lookback cannot be negative")
        if self.market_regime_window < 0:
            raise ValueError("market_regime_window cannot be negative")
        numeric_fields = (
            self.volume_multiplier, self.atr_stop_multiple, self.reward_risk,
            self.cost_bps_per_side, self.slippage_bps_per_side,
        )
        if any(not math.isfinite(value) or value < 0 for value in numeric_fields):
            raise ValueError("multipliers and costs must be finite and non-negative")


@dataclass(frozen=True)
class Trade:
    signal_time: datetime
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    quantity: int
    exit_reason: str
    bars_held: int
    fees: float
    net_pnl: float


def _parse_timestamp(raw: str) -> datetime:
    value = raw.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid ISO timestamp: {raw!r}") from exc


def validate_candles(candles: Sequence[Candle], minimum_bars: int = 1) -> None:
    if len(candles) < minimum_bars:
        raise ValueError(f"insufficient candles: need at least {minimum_bars}, got {len(candles)}")
    previous = None
    for index, candle in enumerate(candles):
        prices = (candle.open, candle.high, candle.low, candle.close, candle.volume)
        if any(not math.isfinite(value) for value in prices):
            raise ValueError(f"row {index}: non-finite numeric value")
        if min(candle.open, candle.high, candle.low, candle.close) <= 0:
            raise ValueError(f"row {index}: prices must be positive")
        if candle.volume < 0:
            raise ValueError(f"row {index}: volume cannot be negative")
        if candle.high < max(candle.open, candle.low, candle.close):
            raise ValueError(f"row {index}: high is below an OHLC value")
        if candle.low > min(candle.open, candle.high, candle.close):
            raise ValueError(f"row {index}: low is above an OHLC value")
        if previous is not None and candle.timestamp <= previous:
            raise ValueError(f"row {index}: timestamps must be strictly increasing")
        previous = candle.timestamp


def load_candles_csv(path: str | Path, minimum_bars: int = 1) -> list[Candle]:
    candles: list[Candle] = []
    with Path(path).open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
            raise ValueError(f"CSV must contain columns: {', '.join(sorted(required))}")
        for row_number, row in enumerate(reader, start=2):
            try:
                candles.append(Candle(
                    timestamp=_parse_timestamp(row["timestamp"]),
                    open=float(row["open"]), high=float(row["high"]),
                    low=float(row["low"]), close=float(row["close"]),
                    volume=float(row["volume"]),
                ))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"CSV row {row_number}: {exc}") from exc
    validate_candles(candles, minimum_bars)
    return candles


def _true_range(candles: Sequence[Candle], index: int) -> float:
    candle = candles[index]
    if index == 0:
        return candle.high - candle.low
    previous_close = candles[index - 1].close
    return max(candle.high - candle.low, abs(candle.high - previous_close), abs(candle.low - previous_close))


def _atr(candles: Sequence[Candle], index: int, window: int) -> float:
    start = index - window + 1
    return sum(_true_range(candles, i) for i in range(start, index + 1)) / window


def run_backtest(
    candles: Sequence[Candle],
    config: StrategyConfig | None = None,
    market_candles: Sequence[Candle] | None = None,
) -> list[Trade]:
    cfg = config or StrategyConfig()
    cfg.validate()
    validate_candles(candles, cfg.minimum_bars)
    market_by_time: dict[datetime, tuple[int, Candle]] = {}
    if cfg.market_regime_window:
        if market_candles is None:
            raise ValueError("market candles are required when market_regime_window is enabled")
        validate_candles(market_candles, cfg.market_regime_window + 1)
        market_by_time = {c.timestamp: (i, c) for i, c in enumerate(market_candles)}
    warmup = max(
        cfg.slow_window + cfg.slow_slope_lookback,
        cfg.breakout_window, cfg.volume_window, cfg.atr_window,
    )
    trades: list[Trade] = []
    signal_index = warmup
    while signal_index < len(candles) - 1:
        signal = candles[signal_index]
        trend_mean = sum(c.close for c in candles[signal_index - cfg.slow_window:signal_index]) / cfg.slow_window
        rising_trend = True
        if cfg.slow_slope_lookback:
            earlier_end = signal_index - cfg.slow_slope_lookback
            earlier_mean = sum(
                c.close for c in candles[earlier_end - cfg.slow_window:earlier_end]
            ) / cfg.slow_window
            rising_trend = trend_mean > earlier_mean
        breakout_high = max(c.high for c in candles[signal_index - cfg.breakout_window:signal_index])
        reference_volume = median(c.volume for c in candles[signal_index - cfg.volume_window:signal_index])
        signal_atr = _atr(candles, signal_index, cfg.atr_window)
        market_supportive = True
        if cfg.market_regime_window:
            matched = market_by_time.get(signal.timestamp)
            market_supportive = False
            if matched is not None:
                market_index, market_bar = matched
                if market_index >= cfg.market_regime_window:
                    prior_market_mean = sum(
                        c.close for c in market_candles[
                            market_index - cfg.market_regime_window:market_index
                        ]
                    ) / cfg.market_regime_window
                    market_supportive = market_bar.close > prior_market_mean
        qualifies = (
            signal.close > trend_mean
            and rising_trend
            and market_supportive
            and signal.close > breakout_high
            and reference_volume > 0
            and signal.volume > cfg.volume_multiplier * reference_volume
            and signal_atr > 0
        )
        if not qualifies:
            signal_index += 1
            continue

        entry_index = signal_index + 1
        entry_raw = candles[entry_index].open
        entry = entry_raw * (1 + cfg.slippage_bps_per_side / 10_000)
        risk = cfg.atr_stop_multiple * signal_atr
        stop = entry - risk
        target = entry + cfg.reward_risk * risk
        if stop <= 0:
            signal_index += 1
            continue

        final_index = min(entry_index + cfg.max_holding_bars - 1, len(candles) - 1)
        exit_index = final_index
        exit_reason = "TIME" if final_index < len(candles) - 1 else "END_OF_DATA"
        exit_raw = candles[final_index].close
        for index in range(entry_index, final_index + 1):
            bar = candles[index]
            # OHLC cannot reveal ordering; assume the adverse event occurred first.
            if bar.low <= stop:
                exit_index, exit_reason, exit_raw = index, "STOP", stop
                break
            if bar.high >= target:
                exit_index, exit_reason, exit_raw = index, "TARGET", target
                break

        exit_price = exit_raw * (1 - cfg.slippage_bps_per_side / 10_000)
        fees = (entry + exit_price) * cfg.quantity * cfg.cost_bps_per_side / 10_000
        net_pnl = (exit_price - entry) * cfg.quantity - fees
        trades.append(Trade(
            signal_time=signal.timestamp, entry_time=candles[entry_index].timestamp,
            exit_time=candles[exit_index].timestamp, entry_price=entry,
            exit_price=exit_price, stop_price=stop, target_price=target,
            quantity=cfg.quantity, exit_reason=exit_reason,
            bars_held=exit_index - entry_index + 1, fees=fees, net_pnl=net_pnl,
        ))
        signal_index = exit_index + 1
    return trades


def performance_metrics(trades: Iterable[Trade]) -> dict[str, float | int | None]:
    items = list(trades)
    pnls = [trade.net_pnl for trade in items]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    equity = peak = drawdown = 0.0
    loss_streak = max_loss_streak = 0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        loss_streak = loss_streak + 1 if pnl < 0 else 0
        max_loss_streak = max(max_loss_streak, loss_streak)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "trades": len(items), "wins": len(wins), "losses": len(losses),
        "win_rate": len(wins) / len(items) if items else None,
        "net_pnl": sum(pnls),
        "expectancy": sum(pnls) / len(items) if items else None,
        "average_win": gross_profit / len(wins) if wins else None,
        "average_loss": sum(losses) / len(losses) if losses else None,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "maximum_drawdown": drawdown,
        "maximum_consecutive_losses": max_loss_streak,
    }


def chronological_report(
    candles: Sequence[Candle], config: StrategyConfig,
    market_candles: Sequence[Candle] | None = None,
) -> dict:
    validate_candles(candles, config.minimum_bars)
    boundaries = (0, int(len(candles) * 0.60), int(len(candles) * 0.80), len(candles))
    names = ("train", "validation", "test")
    report: dict[str, object] = {"config": asdict(config), "bars": len(candles), "segments": {}}
    for name, start, end in zip(names, boundaries, boundaries[1:]):
        segment = candles[start:end]
        if len(segment) < config.minimum_bars:
            report["segments"][name] = {"bars": len(segment), "status": "INSUFFICIENT_DATA"}
        else:
            trades = run_backtest(segment, config, market_candles)
            report["segments"][name] = {
                "bars": len(segment), "status": "OK", "metrics": performance_metrics(trades),
                "trades": [{**asdict(t), "signal_time": t.signal_time.isoformat(),
                            "entry_time": t.entry_time.isoformat(), "exit_time": t.exit_time.isoformat()}
                           for t in trades],
            }
    report["full_sample_metrics"] = performance_metrics(run_backtest(candles, config, market_candles))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline brain research lab; no broker connectivity")
    parser.add_argument("--csv", required=True, help="OHLCV CSV input")
    parser.add_argument("--output", help="optional JSON report path")
    parser.add_argument("--slow-slope-lookback", type=int, default=0,
                        help="require the prior slow mean to exceed its value this many bars earlier")
    parser.add_argument("--market-csv", help="optional completed market-index OHLCV CSV")
    parser.add_argument("--market-regime-window", type=int, default=0)
    args = parser.parse_args()
    config = StrategyConfig(slow_slope_lookback=args.slow_slope_lookback,
                            market_regime_window=args.market_regime_window)
    candles = load_candles_csv(args.csv, config.minimum_bars)
    market_candles = load_candles_csv(args.market_csv, 1) if args.market_csv else None
    report = chronological_report(candles, config, market_candles)
    encoded = json.dumps(report, indent=2, allow_nan=False)
    if args.output:
        Path(args.output).write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
