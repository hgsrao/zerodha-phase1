"""Deterministically resample validated 15-minute candles into complete hourly bars."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import time
from pathlib import Path

from brain_research_lab import Candle, load_candles_csv, validate_candles


SESSION_START = time(9, 15)
def resample_hourly(candles: list[Candle]) -> list[Candle]:
    days: dict[object, list[Candle]] = defaultdict(list)
    for candle in candles:
        days[candle.timestamp.date()].append(candle)
    result: list[Candle] = []
    for day in sorted(days):
        bars = days[day]
        for offset in range(0, 24, 4):
            group = bars[offset:offset + 4]
            if len(group) != 4:
                continue
            expected_hour = 9 + ((15 + offset * 15) // 60)
            expected_minute = (15 + offset * 15) % 60
            if group[0].timestamp.time().replace(tzinfo=None) != time(expected_hour, expected_minute):
                continue
            if any((group[i].timestamp - group[i - 1].timestamp).total_seconds() != 900 for i in range(1, 4)):
                continue
            result.append(Candle(
                timestamp=group[0].timestamp,
                open=group[0].open,
                high=max(c.high for c in group),
                low=min(c.low for c in group),
                close=group[-1].close,
                volume=sum(c.volume for c in group),
            ))
    if result:
        validate_candles(result, 1)
    return result


def write_csv(path: Path, candles: list[Candle]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("timestamp", "open", "high", "low", "close", "volume"))
        for candle in candles:
            writer.writerow((candle.timestamp.isoformat(), candle.open, candle.high,
                             candle.low, candle.close, candle.volume))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", default="historical_data_research_ready")
    parser.add_argument("--output-dir", default="historical_data_60minute")
    args = parser.parse_args()
    for source in sorted(Path(args.source_dir).glob("NSE_*_15minute_*.csv")):
        candles = resample_hourly(load_candles_csv(source, 100))
        target = Path(args.output_dir) / source.name.replace("15minute", "60minute")
        write_csv(target, candles)
        print(f"{source.name}: {len(candles)} complete hourly bars -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
