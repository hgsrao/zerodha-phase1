#!/usr/bin/env python3
"""Production-trading engine harness using real frozen historical data.

This is a real-data execution path for a single symbol, designed for diagnostics and
calibration-oriented engine runs. It is intentionally fail-closed: it will refuse to
run if the real dataset is missing or malformed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from ecs_runtime_v2 import ECSRuntimeV2
from runtime.operating_mode import OperatingMode, RuntimeConfig, SafeBrokerAdapter, StartupGate


DEFAULT_DATA_DIR = "/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824"


@dataclass
class EngineSummary:
    symbol: str
    rows: int
    start: str
    end: str
    runtime_ok: bool
    runtime_decision: str
    ready_boxes: int
    trade_count: int
    total_pnl: float
    avg_pnl: float
    win_rate: float
    final_cash: float
    last_close: float


class ProductionTradingEngine:
    """Fail-closed production-ready harness driven by real dataset + active runtime."""

    def __init__(self, symbol: str = "ADANIENT", data_dir: str = DEFAULT_DATA_DIR):
        self.symbol = symbol
        self.data_dir = data_dir
        self.registry = CanonicalParameterRegistry()
        self.runtime = ECSRuntimeV2()

    def load_real_symbol_data(self) -> pd.DataFrame:
        csv_name = f"NSE_{self.symbol}_minute_2023-07-03_2026-08-24.csv"
        path = os.path.join(self.data_dir, csv_name)
        if not os.path.exists(path):
            raise FileNotFoundError(f"real historical dataset for {self.symbol} not found at {path}")

        df = pd.read_csv(path)
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        missing = sorted(required - set(df.columns))
        if missing:
            raise ValueError(f"dataset missing required columns: {missing}")

        df = df.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=False)
        df = df.sort_values("timestamp").reset_index(drop=True)

        if df.empty:
            raise ValueError("dataset is empty")
        if df["timestamp"].duplicated().any():
            raise ValueError("duplicate timestamps found in source dataset")
        if (df[["open", "high", "low", "close"]] <= 0).any().any():
            raise ValueError("non-positive OHLC values found")
        if (df["volume"] < 0).any():
            raise ValueError("negative volume values found")

        return df

    def build_runtime_config(self) -> Dict[str, Any]:
        base = {name: spec.default for name, spec in self.registry.params.items()}
        base["symbols_to_trade"] = [self.symbol]
        base["exclude_symbols"] = []
        base["data_validation_mode"] = "strict"
        base["order_type"] = "MARKET"
        base["trading_hours_start"] = "09:15"
        base["trading_hours_end"] = "15:30"
        base["capital_allocation_mode"] = "equal"
        return base

    def validate_runtime(self, config: Dict[str, Any]) -> Dict[str, Any]:
        report = self.runtime.run_cycle(effective_config=config)
        if not report.get("ok"):
            raise ValueError(f"runtime rejected config: {report}")
        return report

    def _signal_features(self, df: pd.DataFrame, config: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        config = config or {}
        out = df.copy()
        out["ret"] = out["close"].pct_change().fillna(0.0)
        mom_period = int(config.get("momentum_calculation_period", 20))
        vol_period = int(config.get("atr_calculation_period", 20))
        out["mom"] = out["close"].pct_change(mom_period).fillna(0.0)
        out["vol"] = out["ret"].rolling(vol_period).std().fillna(0.0)
        out["signal"] = np.where(out["vol"] > 0, out["mom"] / out["vol"], 0.0)
        return out

    def run_trading_cycle(self, df: pd.DataFrame, effective_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        config = effective_config or {}
        data = self._signal_features(df, config)
        start_cash = 100000.0
        cash = start_cash
        position_qty = 0
        entry_price = 0.0
        entry_ts = None
        entry_signal = 0.0
        entry_idx = 0
        trades: List[Dict[str, Any]] = []

        entry_threshold = float(config.get("entry_confidence_threshold", 0.25))
        exit_threshold = float(config.get("exit_confidence_threshold", 0.0))
        signal_momentum_threshold = float(config.get("signal_persistence_requirement", 0.00025))
        capital_fraction = float(config.get("capital_per_trade_fraction", 0.02))
        stop_pct = float(config.get("stop_loss_atr_mult", 0.0035))
        target_pct = float(config.get("profit_target_atr_mult", 0.0070))
        max_hold = int(config.get("max_hold_bars", 180))

        for idx, row in data.iterrows():
            price = float(row["close"])
            sig = float(row["signal"])

            if position_qty == 0:
                if sig > entry_threshold and float(row["mom"]) > signal_momentum_threshold:
                    qty = max(1, int(cash * capital_fraction / max(price, 1.0)))
                    position_qty = qty
                    entry_price = price
                    entry_ts = row["timestamp"]
                    entry_signal = sig
                    entry_idx = idx
                    cash -= qty * price * (1 + 0.0002)
            else:
                pnl_pct = (price - entry_price) / entry_price
                exit_now = False
                if pnl_pct <= -stop_pct or pnl_pct >= target_pct or idx - entry_idx >= max_hold or (exit_threshold > 0 and sig < exit_threshold):
                    exit_now = True

                if exit_now:
                    exit_ts = row["timestamp"]
                    exit_price = price
                    cash += position_qty * exit_price * (1 - 0.0002)
                    pnl = (exit_price - entry_price) * position_qty
                    trades.append({
                        "entry_ts": entry_ts,
                        "exit_ts": exit_ts,
                        "entry_price": round(entry_price, 4),
                        "exit_price": round(exit_price, 4),
                        "qty": position_qty,
                        "pnl": round(float(pnl), 2),
                        "signal": round(float(entry_signal), 6),
                        "hold_bars": idx - entry_idx,
                    })
                    position_qty = 0
                    entry_price = 0.0
                    entry_ts = None
                    entry_signal = 0.0
                    entry_idx = 0

        if position_qty > 0:
            last_price = float(data.iloc[-1]["close"])
            cash += position_qty * last_price * (1 - 0.0002)
            pnl = (last_price - entry_price) * position_qty
            trades.append({
                "entry_ts": entry_ts,
                "exit_ts": data.iloc[-1]["timestamp"],
                "entry_price": round(entry_price, 4),
                "exit_price": round(last_price, 4),
                "qty": position_qty,
                "pnl": round(float(pnl), 2),
                "signal": round(float(entry_signal), 6),
                "hold_bars": len(data) - entry_idx,
            })

        realized = pd.DataFrame(trades)
        trade_count = len(realized)
        total_pnl = float(realized["pnl"].sum()) if trade_count else 0.0
        avg_pnl = float(realized["pnl"].mean()) if trade_count else 0.0
        win_rate = float((realized["pnl"] > 0).mean() * 100.0) if trade_count else 0.0
        equity = cash + (realized["pnl"].sum() if trade_count else 0.0)

        return {
            "trade_count": trade_count,
            "total_pnl": total_pnl,
            "avg_pnl": avg_pnl,
            "win_rate": win_rate,
            "final_cash": float(cash),
            "equity": float(equity),
            "last_close": float(data.iloc[-1]["close"]),
            "trades": trades,
        }

    def run(self) -> EngineSummary:
        df = self.load_real_symbol_data()
        config = self.build_runtime_config()
        runtime_report = self.validate_runtime(config)
        trading_report = self.run_trading_cycle(df)
        return EngineSummary(
            symbol=self.symbol,
            rows=len(df),
            start=str(df["timestamp"].min()),
            end=str(df["timestamp"].max()),
            runtime_ok=bool(runtime_report["ok"]),
            runtime_decision=str(runtime_report.get("decision", "UNKNOWN")),
            ready_boxes=sum(1 for box in runtime_report["black_box_statuses"] if box["status"] == "READY"),
            trade_count=int(trading_report["trade_count"]),
            total_pnl=float(trading_report["total_pnl"]),
            avg_pnl=float(trading_report["avg_pnl"]),
            win_rate=float(trading_report["win_rate"]),
            final_cash=float(trading_report["final_cash"]),
            last_close=float(trading_report["last_close"]),
        )


def main() -> None:
    engine = ProductionTradingEngine(symbol="ADANIENT", data_dir=DEFAULT_DATA_DIR)
    summary = engine.run()
    print("SYMBOL=", summary.symbol)
    print("ROWS=", summary.rows)
    print("START=", summary.start)
    print("END=", summary.end)
    print("RUNTIME_OK=", summary.runtime_ok)
    print("RUNTIME_DECISION=", summary.runtime_decision)
    print("READY_BOXES=", summary.ready_boxes)
    print("TRADE_COUNT=", summary.trade_count)
    print("TOTAL_PNL=", round(summary.total_pnl, 2))
    print("AVG_PNL=", round(summary.avg_pnl, 2))
    print("WIN_RATE=", round(summary.win_rate, 2))
    print("FINAL_CASH=", round(summary.final_cash, 2))
    print("LAST_CLOSE=", round(summary.last_close, 4))


if __name__ == "__main__":
    main()
