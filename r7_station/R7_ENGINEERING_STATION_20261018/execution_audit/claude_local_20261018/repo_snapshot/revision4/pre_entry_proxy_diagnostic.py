"""Read-only decision-bar proxy analysis for observed candidate fills."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from revision4.validate_48symbol_sealed import DATA_DIR, MANIFEST_PATH
from revision4.validate_orchestrator import ManifestDataLoader

RAW = Path("diagnostic_output/raw_observations.csv")
OUT = Path("diagnostic_output/pre_entry_proxy_diagnostic.json")
FEATURES = Path("diagnostic_output/pre_entry_proxy_features.csv")


def run(raw_path=RAW, output_path=OUT, features_path=FEATURES):
    raw = pd.read_csv(raw_path)
    required = {"symbol", "decision_timestamp", "slippage_pct"}
    if not required.issubset(raw):
        raise ValueError(f"raw observations missing {sorted(required - set(raw))}")
    raw["decision_timestamp"] = pd.to_datetime(raw["decision_timestamp"], utc=True)
    loader = ManifestDataLoader(MANIFEST_PATH, DATA_DIR)
    rows = []
    for symbol, observations in raw.groupby("symbol"):
        bars = loader.load_symbol_data(symbol).copy()
        bars["tr"] = pd.concat([
            bars.high - bars.low,
            (bars.high - bars.close.shift()).abs(),
            (bars.low - bars.close.shift()).abs(),
        ], axis=1).max(axis=1)
        bars["atr"] = bars.tr.rolling(20, min_periods=20).mean()
        bars["date"] = bars.timestamp.dt.date
        bars["typical_volume_20"] = bars.volume.rolling(20, min_periods=20).mean()
        bars["cum_pv"] = (bars.close * bars.volume).groupby(bars["date"]).cumsum()
        bars["cum_volume"] = bars.volume.groupby(bars["date"]).cumsum()
        bars["session_vwap"] = bars.cum_pv / bars.cum_volume
        indexed = bars.set_index("timestamp")
        for observation in observations.itertuples(index=False):
            if observation.decision_timestamp not in indexed.index:
                continue
            bar = indexed.loc[observation.decision_timestamp]
            bar_range = bar.high - bar.low
            rows.append({
                "symbol": symbol, "decision_timestamp": observation.decision_timestamp.isoformat(),
                "slippage_pct": float(observation.slippage_pct),
                "range_atr": None if not pd.notna(bar.atr) or bar.atr == 0 else float(bar_range / bar.atr),
                "volume_ratio": None if not pd.notna(bar.typical_volume_20) or bar.typical_volume_20 == 0 else float(bar.volume / bar.typical_volume_20),
                "vwap_range_deviation": None if bar_range == 0 else float(abs(bar.close - bar.session_vwap) / bar_range),
            })
    frame = pd.DataFrame(rows).dropna()
    if frame.empty:
        raise RuntimeError("no candidate rows had complete decision-bar features")
    breach = frame[frame.slippage_pct > 0.15]
    def summary(part):
        return {key: {f"p{p}": float(part[key].quantile(p / 100)) for p in (50, 90, 95, 99)}
                for key in ("range_atr", "volume_ratio", "vwap_range_deviation")}
    tradeoffs = {}
    for threshold in (3.0, 4.0, 5.0, 6.0):
        flagged = frame.range_atr > threshold
        breaches = frame.slippage_pct > 0.15
        tradeoffs[f"range_atr_gt_{threshold:.1f}"] = {
            "flagged_candidates": int(flagged.sum()),
            "flagged_rate_pct": round(float(flagged.mean() * 100), 4),
            "v3_slippage_breaches_caught": int((flagged & breaches).sum()),
            "v3_slippage_breach_recall_pct": round(float((flagged & breaches).sum() / breaches.sum() * 100), 4),
            "non_breach_candidates_rejected": int((flagged & ~breaches).sum()),
        }
    frame.to_csv(features_path, index=False)
    result = {
        "method": "completed decision-bar only; ATR(20) and session VWAP; no future prices used as features",
        "candidate_count": len(frame), "v3_breach_count": len(breach),
        "feature_rows_path": str(features_path),
        "population_percentiles": summary(frame),
        "v3_breach_percentiles": summary(breach) if not breach.empty else {},
        "range_atr_tradeoffs": tradeoffs,
        "maxhealth_rows": frame[frame.symbol == "MAXHEALTH"].sort_values("slippage_pct", ascending=False).head(5).to_dict("records"),
    }
    output_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
