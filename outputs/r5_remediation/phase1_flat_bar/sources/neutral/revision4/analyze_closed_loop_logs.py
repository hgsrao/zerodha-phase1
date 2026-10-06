"""Read-only causal feature analysis of prior sealed validation trade logs."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from revision4.validate_48symbol_sealed import DATA_DIR, MANIFEST_PATH

SOURCE = Path("diagnostic_output/ray_optuna_corrected_202309_attribution")
OUT = SOURCE / "closed_loop_evidence.json"
FEATURE_ROWS = SOURCE / "closed_loop_feature_rows.csv"


def _features_for_symbol(symbol: str, trades: pd.DataFrame, filename: str) -> list[dict]:
    """Use only the completed decision bar preceding each next-open fill."""
    columns = ["timestamp", "open", "high", "low", "close", "volume"]
    bars = pd.read_csv(Path(DATA_DIR) / filename, usecols=columns)
    bars["timestamp"] = pd.to_datetime(bars["timestamp"], utc=True)
    begin = pd.Timestamp("2023-10-02", tz="UTC") - pd.Timedelta(30, unit="min")
    end = pd.Timestamp("2023-10-07", tz="UTC")
    bars = bars[(bars.timestamp >= begin) & (bars.timestamp < end)].sort_values("timestamp")
    bars = bars.reset_index(drop=True)
    previous_close = bars.close.shift(1)
    true_range = pd.concat([
        bars.high - bars.low, (bars.high - previous_close).abs(), (bars.low - previous_close).abs(),
    ], axis=1).max(axis=1)
    bars["atr20"] = true_range.rolling(20, min_periods=20).mean()
    bars["range_atr"] = (bars.high - bars.low) / bars.atr20
    bars["volume_ratio"] = bars.volume / bars.volume.shift(1).rolling(20, min_periods=20).mean()
    bars["move5_atr"] = (bars.close - bars.close.shift(5)) / bars.atr20
    session = bars.timestamp.dt.date
    bars["vwap"] = (bars.close * bars.volume).groupby(session).cumsum() / bars.volume.groupby(session).cumsum()
    bars["vwap_gap_atr"] = (bars.close - bars.vwap) / bars.atr20
    lookup = bars.set_index("timestamp")
    rows = []
    for trade in trades.itertuples(index=False):
        decision = trade.entry_timestamp - pd.Timedelta(1, unit="min")
        if decision not in lookup.index:
            continue
        bar = lookup.loc[decision]
        rows.append({
            "finalist": int(trade.finalist), "trade_id": trade.trade_id, "symbol": symbol,
            "exit_reason": trade.exit_reason, "net_pnl": float(trade.net_pnl),
            "entry_hour_ist": int(trade.entry_timestamp.tz_convert("Asia/Kolkata").hour),
            "hold_minutes": float((trade.exit_timestamp - trade.entry_timestamp).total_seconds() / 60),
            "range_atr": float(bar.range_atr), "volume_ratio": float(bar.volume_ratio),
            "signed_move5_atr": float(bar.move5_atr * trade.direction),
            "signed_vwap_gap_atr": float(bar.vwap_gap_atr * trade.direction),
        })
    return rows


def _aggregate(frame: pd.DataFrame, group: str) -> list[dict]:
    values = frame.groupby(group).agg(
        trades=("trade_id", "size"), net_pnl=("net_pnl", "sum"),
        median_hold_minutes=("hold_minutes", "median"),
        median_range_atr=("range_atr", "median"),
        median_volume_ratio=("volume_ratio", "median"),
        median_signed_move5_atr=("signed_move5_atr", "median"),
        median_signed_vwap_gap_atr=("signed_vwap_gap_atr", "median"),
    ).reset_index()
    return json.loads(values.round(6).to_json(orient="records"))


def main() -> None:
    manifest = json.loads(Path(MANIFEST_PATH).read_text())
    files = {item["symbol"]: item["filename"] for item in manifest["files"]}
    trades = []
    for finalist in (1, 2):
        report = json.loads((SOURCE / f"finalist_{finalist}.json").read_text())
        for trade in report["sealed_run"]["completed_trade_ledger"]:
            trade = dict(trade)
            trade["finalist"] = finalist
            trade["entry_timestamp"] = pd.Timestamp(trade["entry_timestamp"])
            trade["exit_timestamp"] = pd.Timestamp(trade["exit_timestamp"])
            trades.append(trade)
    trade_frame = pd.DataFrame(trades)
    features = []
    for symbol, symbol_trades in trade_frame.groupby("symbol", sort=True):
        features.extend(_features_for_symbol(symbol, symbol_trades, files[symbol]))
    frame = pd.DataFrame(features).dropna()
    frame.to_csv(FEATURE_ROWS, index=False)
    output = {
        "method": "decision-bar features only; no future prices used as inputs",
        "joined_trade_count": len(frame), "source_trade_count": len(trade_frame),
        "feature_rows_path": str(FEATURE_ROWS),
        "limitations": [
            "prior ledger does not preserve per-trade PID adjustments",
            "prior ledger does not preserve Grid Sync or Nifty state",
            "features are descriptive evidence, not an approved control rule",
        ],
        "by_finalist": {
            str(number): {
                "by_exit_reason": _aggregate(frame[frame.finalist == number], "exit_reason"),
                "by_entry_hour_ist": _aggregate(frame[frame.finalist == number], "entry_hour_ist"),
            } for number in (1, 2)
        },
        "stop_vs_target_combined": _aggregate(
            frame[frame.exit_reason.isin(["STOP_HIT", "TARGET_HIT"])], "exit_reason"
        ),
    }
    OUT.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
