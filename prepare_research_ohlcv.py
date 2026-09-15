"""Create research-ready OHLCV copies while preserving immutable raw downloads."""

from __future__ import annotations

import csv
from pathlib import Path


FIELDS = ("timestamp", "open", "high", "low", "close", "volume")
AUDIT_FIELDS = ("file", "timestamp", "field", "original", "corrected", "reason")


def prepare(source_dir: Path, output_dir: Path) -> list[dict]:
    audit: list[dict] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    for source in sorted(source_dir.glob("*.csv")):
        with source.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        cleaned = []
        previous = None
        for row in rows:
            if previous is not None and row["timestamp"] <= previous:
                raise ValueError(f"{source.name}: timestamps are not strictly increasing")
            previous = row["timestamp"]
            values = {name: float(row[name]) for name in ("open", "high", "low", "close")}
            corrected_high = max(values.values())
            corrected_low = min(values.values())
            if values["high"] != corrected_high:
                audit.append({"file": source.name, "timestamp": row["timestamp"],
                              "field": "high", "original": row["high"],
                              "corrected": corrected_high,
                              "reason": "high below another OHLC field"})
                row["high"] = str(corrected_high)
            if values["low"] != corrected_low:
                audit.append({"file": source.name, "timestamp": row["timestamp"],
                              "field": "low", "original": row["low"],
                              "corrected": corrected_low,
                              "reason": "low above another OHLC field"})
                row["low"] = str(corrected_low)
            cleaned.append(row)
        target = output_dir / source.name
        with target.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(cleaned)
    with (output_dir / "correction_audit.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=AUDIT_FIELDS)
        writer.writeheader()
        writer.writerows(audit)
    return audit


if __name__ == "__main__":
    changes = prepare(Path("historical_data"), Path("historical_data_research_ready"))
    print(f"Prepared research copies; documented corrections: {len(changes)}")
