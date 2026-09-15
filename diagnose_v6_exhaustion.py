"""Diagnostic decomposition of Version-6 selections; does not create a strategy."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import median

from brain_research_lab import load_candles_csv


def mean(values):
    return sum(values) / len(values) if values else None


def main() -> int:
    report = json.loads(Path("brain_results_v6/cross_sectional_v6.json").read_text())
    paths = list(Path("historical_data_60minute").glob("NSE_*60minute*.csv"))
    paths += list(Path("historical_data_v5_additional_60minute").glob("NSE_*60minute*.csv"))
    series = {path.name.split("_", 2)[1]: load_candles_csv(path, 100) for path in paths}
    indices = {symbol: {bar.timestamp.isoformat(): i for i, bar in enumerate(bars)}
               for symbol, bars in series.items()}
    grouped = defaultdict(list)
    for row in report["selections"]:
        grouped[row["timestamp"]].append(row)

    observations = []
    for rows in grouped.values():
        rows.sort(key=lambda row: row["score"], reverse=True)
        for rank, row in enumerate(rows, start=1):
            bars = series[row["symbol"]]
            index = indices[row["symbol"]][row["timestamp"]]
            if index + 30 >= len(bars):
                continue
            signal_close = bars[index].close
            next_open = bars[index + 1].open
            observations.append({
                "rank": rank, "score": row["score"],
                "overnight_gap": next_open / signal_close - 1,
                "one_session": bars[index + 6].close / next_open - 1,
                "three_sessions": bars[index + 18].close / next_open - 1,
                "five_sessions": bars[index + 30].close / next_open - 1,
            })
    scores = sorted(item["score"] for item in observations)
    cut1, cut2, cut3 = (scores[int(len(scores) * fraction)] for fraction in (.25, .5, .75))
    result = {
        "by_rank": {}, "by_score_quartile": {},
        "score_quartile_boundaries": [cut1, cut2, cut3],
    }
    for rank in (1, 2):
        rows = [item for item in observations if item["rank"] == rank]
        result["by_rank"][str(rank)] = {
            key: mean([row[key] for row in rows])
            for key in ("overnight_gap", "one_session", "three_sessions", "five_sessions")
        }
    quartiles = (
        ("Q1_LOW", lambda score: score <= cut1),
        ("Q2", lambda score: cut1 < score <= cut2),
        ("Q3", lambda score: cut2 < score <= cut3),
        ("Q4_HIGH", lambda score: score > cut3),
    )
    for name, predicate in quartiles:
        rows = [item for item in observations if predicate(item["score"])]
        result["by_score_quartile"][name] = {
            "observations": len(rows),
            **{key: mean([row[key] for row in rows]) for key in (
                "overnight_gap", "one_session", "three_sessions", "five_sessions"
            )},
        }
    output = Path("brain_results_v6/v6_exhaustion_diagnostic.json")
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
