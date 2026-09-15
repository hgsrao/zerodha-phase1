"""Frozen Version-5 multi-symbol, six-month walk-forward robustness report."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from brain_research_lab import StrategyConfig, load_candles_csv, run_backtest


WINDOWS = (
    (date(2024, 2, 14), date(2024, 8, 14)),
    (date(2024, 8, 14), date(2025, 2, 14)),
    (date(2025, 2, 14), date(2025, 8, 14)),
    (date(2025, 8, 14), date(2026, 2, 14)),
    (date(2026, 2, 14), date(2026, 8, 14)),
)


def symbol_from_path(path: Path) -> str:
    return path.name.split("_", 2)[1]


def r_value(trade) -> float:
    initial_risk = (trade.entry_price - trade.stop_price) * trade.quantity
    if initial_risk <= 0:
        raise ValueError("non-positive initial trade risk")
    return trade.net_pnl / initial_risk


def stats(values: list[float]) -> dict:
    gains = sum(value for value in values if value > 0)
    losses = abs(sum(value for value in values if value < 0))
    return {
        "trades": len(values),
        "total_r": sum(values),
        "expectancy_r": sum(values) / len(values) if values else None,
        "profit_factor_r": gains / losses if losses else None,
    }


def main() -> int:
    market_path = next(Path("historical_data_market_60minute").glob("NSE_NIFTY*60minute*.csv"))
    market = load_candles_csv(market_path, 100)
    equity_paths = sorted(Path("historical_data_60minute").glob("NSE_*60minute*.csv"))
    equity_paths += sorted(Path("historical_data_v5_additional_60minute").glob("NSE_*60minute*.csv"))
    if len(equity_paths) != 20:
        raise RuntimeError(f"expected frozen universe of 20 symbols, got {len(equity_paths)}")

    config = StrategyConfig(market_regime_window=50)
    rows = []
    all_values: list[float] = []
    by_symbol: dict[str, list[float]] = {}
    by_window: dict[str, list[float]] = {f"{a}_{b}": [] for a, b in WINDOWS}
    for path in equity_paths:
        symbol = symbol_from_path(path)
        candles = load_candles_csv(path, 100)
        trades = run_backtest(candles, config, market)
        symbol_values: list[float] = []
        for start, end in WINDOWS:
            key = f"{start}_{end}"
            values = [r_value(t) for t in trades if start <= t.signal_time.date() < end]
            result = stats(values)
            rows.append({"symbol": symbol, "window_start": start.isoformat(),
                         "window_end_exclusive": end.isoformat(), **result})
            symbol_values.extend(values)
            by_window[key].extend(values)
        by_symbol[symbol] = symbol_values
        all_values.extend(symbol_values)

    symbol_stats = {symbol: stats(values) for symbol, values in by_symbol.items()}
    window_stats = {window: stats(values) for window, values in by_window.items()}
    profitable_windows = sum(1 for result in window_stats.values() if result["total_r"] > 0)
    report = {
        "research_version": "V5_WALK_FORWARD",
        "config": asdict(config),
        "symbols": sorted(by_symbol),
        "combined": stats(all_values),
        "profitable_windows": profitable_windows,
        "total_windows": len(window_stats),
        "profitable_window_rate": profitable_windows / len(window_stats),
        "by_window": window_stats,
        "by_symbol": symbol_stats,
        "symbol_windows": rows,
    }
    output = Path("brain_results_v5")
    output.mkdir(exist_ok=True)
    (output / "walk_forward_v5.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    with (output / "symbol_windows_v5.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({key: report[key] for key in (
        "combined", "profitable_windows", "total_windows", "profitable_window_rate"
    )}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
