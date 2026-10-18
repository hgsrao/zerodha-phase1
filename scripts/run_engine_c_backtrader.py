"""Engine C: run the shared R5 CCPP engine inside a Backtrader backtest (offline only).

    # seeded synthetic smoke feed (no market data needed; P&L meaningless)
    PYTHONPATH="$PWD" python scripts/run_engine_c_backtrader.py --synthetic-smoke --bars 500
    # real frozen data (operator machine), INFY, last 1,200 bars
    PYTHONPATH="$PWD" python scripts/run_engine_c_backtrader.py --symbols INFY --bars 1200 \\
        --data-dir /path/to/frozen_48_1min_20230703_20260824 --grid-manifest local_workspace/records/grid-manifest-local.json
    # all 48 frozen symbols
    PYTHONPATH="$PWD" python scripts/run_engine_c_backtrader.py --symbols ALL --bars 1200 ...

Writes a machine-readable JSON report.  Exit code 0 only with zero reconciliation breaks.
No broker network access, no Kite, no live trading, no market-data download.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from runtime.live_trading_guard import require_live_trading_disabled
    require_live_trading_disabled()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbols", default="INFY", help="comma list, or ALL for the 48-symbol frozen universe")
    parser.add_argument("--bars", type=int, default=1200, help="last N bars per symbol (warm-up included)")
    parser.add_argument("--warmup", type=int, default=60, help="completed bars per symbol before any decision")
    parser.add_argument("--synthetic-smoke", action="store_true", help="seeded synthetic feed shared with Engine B")
    parser.add_argument("--data-dir", default=os.environ.get("DATA_DIR"),
                        help="frozen dataset directory (default: $DATA_DIR, else the manifest's data_dir)")
    parser.add_argument("--grid-manifest", type=Path, default=ROOT / "local_workspace/records/grid-manifest-local.json")
    parser.add_argument("--governor-authority", choices=["full", "advisory"], default="full")
    parser.add_argument("--telemetry", choices=["full", "compact"], default="full")
    parser.add_argument("--slippage-perc", type=float, default=None, help="override Engine C slippage fraction")
    parser.add_argument("--volume-fill-perc", type=float, default=None,
                        help="Backtrader FixedBarPerc filler: max %% of bar volume filled per bar")
    parser.add_argument("--report", type=Path, default=ROOT / "local_workspace/validation/engine_c/engine_c_report.json")
    from r5_authority_cli import add_authority_arguments, authority_kwargs
    add_authority_arguments(parser)
    args = parser.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.bars <= args.warmup:
        parser.error("--bars must exceed --warmup")

    from dataclasses import replace
    from engine_c.config import EngineCConfig
    from engine_c.data_contract import DataContractError, load_real, load_synthetic
    from engine_c.runner import run_engine_c

    try:
        if args.synthetic_smoke:
            data, nifty, vix, inputs = load_synthetic(symbols, args.bars, args.warmup)
        else:
            data, nifty, vix, inputs = load_real(symbols, args.bars, args.data_dir, args.grid_manifest, args.warmup)
    except DataContractError as exc:
        print(f"ENGINE C REFUSED: {exc}")
        return 2
    config = EngineCConfig()
    if args.slippage_perc is not None:
        config = replace(config, slippage_perc=args.slippage_perc)
    if args.volume_fill_perc is not None:
        config = replace(config, volume_fill_perc=args.volume_fill_perc)
    report = run_engine_c(data, nifty, vix, warmup=args.warmup, config=config,
                          orchestrator_kwargs={**authority_kwargs(args), "governor_authority": args.governor_authority,
                                               "telemetry_mode": args.telemetry})
    report["source"] = inputs["source"]
    report["data_audit"] = inputs["audit"]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, default=str))

    o, acct, led, rec = report["orders"], report["backtrader_accounting"], report["r5_ledger"], report["reconciliation"]
    print(f"ENGINE C (Backtrader native execution)  {inputs['source'].get('kind')}  symbols={len(report['symbols'])}")
    print(f"  bars received {report['bars_received']} (warm-up {report['warmup_bars']})  "
          f"decision ticks {report['decision_ticks']}  first decision {report['first_decision_label']}")
    print(f"  orders submitted {o['submitted']} (entry {o['entry_submitted']}, exit {o['exit_submitted']})  "
          f"accepted {o['accepted']}  rejected {o['rejected']}  margin {o['margin']}  canceled {o['canceled']}  "
          f"fill events {o['fill_events']}  partial entry {o['entry_partial_fills']}  "
          f"positions closed {o['positions_closed']}  capacity holds {o['entry_capacity_rejections']}  "
          f"working at end {o['working_at_end']}")
    print(f"  Backtrader: net {acct['net_pnl']:.2f}  realized gross {acct['realized_gross']:.2f}  "
          f"commissions {acct['commissions']:.2f}  unrealized {acct['unrealized']:.2f}  "
          f"open {sorted(acct['open_positions'])}  max DD {acct['max_drawdown_fraction']:.4%}")
    print(f"  R5 ledger: trades {led['completed_trades']}  gross {led['gross_pnl']:.2f}  costs {led['costs']:.2f}  "
          f"net {led['net_pnl']:.2f}  exits {led['exit_reasons']}")
    final = rec["final"]["rows"]
    print(f"  reconciliation: checks {rec['checks']}  breaks {len(rec['breaks'])}  final "
          + ", ".join(f"{k}={v['status']}" for k, v in final.items()))
    print(f"  report: {args.report}")
    return 1 if rec["breaks"] or rec["ledger_gross_error"] or rec["chronology_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
