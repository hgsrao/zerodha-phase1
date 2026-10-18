"""Engine A reference report: the unchanged shared orchestrator run() with its offline paper broker
(CostedPaperBrokerAdapter), on exactly the data contract Engine C uses.  Read-only with respect to
Engine A's code; it only writes a report that scripts/compare_r5_engines.py can read.

    PYTHONPATH="$PWD" python scripts/run_engine_a_reference.py --synthetic-smoke --bars 500
    PYTHONPATH="$PWD" python scripts/run_engine_a_reference.py --symbols INFY --bars 1200 --data-dir ...
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EXECUTION_MODEL = {
    "engine": "ENGINE_A_PAPER_BROKER",
    "venue": "CostedPaperBrokerAdapter (synchronous offline paper broker)",
    "decision_time": "close of the completed 1-minute bar",
    "entry_reference_price": "next bar's open (read from the replay frame)",
    "entry_fill": "synchronous paper fill at the next bar's open +/- paper slippage band",
    "exit_fill": "synchronous paper fill at the R5 exit price (stop/target level intrabar) +/- slippage",
    "same_bar_assumption": "stop/target fills at the level on the bar that touched it",
    "partial_fills": "none",
    "end_of_data": "open positions force-closed at the final close (end_of_run_reconciliation)",
}


def main() -> int:
    from runtime.live_trading_guard import require_live_trading_disabled
    require_live_trading_disabled()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbols", default="INFY")
    parser.add_argument("--bars", type=int, default=1200)
    parser.add_argument("--warmup", type=int, default=60)
    parser.add_argument("--synthetic-smoke", action="store_true")
    parser.add_argument("--data-dir", default=os.environ.get("DATA_DIR"))
    parser.add_argument("--grid-manifest", type=Path, default=ROOT / "local_workspace/records/grid-manifest-local.json")
    parser.add_argument("--governor-authority", choices=["full", "advisory"], default="full")
    parser.add_argument("--report", type=Path, default=ROOT / "local_workspace/validation/engine_a/engine_a_report.json")
    sys.path.insert(0, str(ROOT / "scripts"))
    from r5_authority_cli import add_authority_arguments, authority_kwargs
    add_authority_arguments(parser)
    args = parser.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    import pandas as pd
    from engine_c.data_contract import DataContractError, load_real, load_synthetic
    from engine_c.runner import data_identity
    from revision2_external.grid_context import SealedGridContextProvider
    from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
    from revision5.ccpp_unified_plant import CentralPlantMasterDCS

    try:
        if args.synthetic_smoke:
            data, nifty, vix, inputs = load_synthetic(symbols, args.bars, args.warmup)
        else:
            data, nifty, vix, inputs = load_real(symbols, args.bars, args.data_dir, args.grid_manifest, args.warmup)
    except DataContractError as exc:
        print(f"ENGINE A REFERENCE REFUSED: {exc}")
        return 2
    plant = CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:")
    engine = Revision2ExternalEngineOrchestrator(
        sorted(data), grid_context_provider=SealedGridContextProvider(nifty, vix), real_plant_dcs=plant,
        plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper", telemetry_mode="full",
        governor_authority=args.governor_authority, starting_equity=1_000_000.0, **authority_kwargs(args))
    r5 = engine.run({s: data[s] for s in sorted(data)}, warmup=args.warmup)
    trades = r5["trades"]
    report = {
        "engine": "ENGINE_A_PAPER_BROKER",
        "execution_model": EXECUTION_MODEL,
        "symbols": sorted(data),
        "data_identity": data_identity(data),
        "bars_received": int(sum(len(f) for f in data.values())),
        "warmup_bars": args.warmup * len(data),
        "orders": {"entry_submitted": r5["orders_submitted"], "exit_submitted": r5["exit_orders_submitted"],
                   "fills": r5["fills"]},
        "r5_ledger": {"completed_trades": len(trades), "gross_pnl": r5["gross_pnl"],
                      "costs": sum(t["costs"] for t in trades), "net_pnl": r5["net_pnl"],
                      "exit_reasons": {str(k): int(v) for k, v in pd.Series([t["reason"] for t in trades]).value_counts().items()} if trades else {},
                      "mtm_max_drawdown_fraction": r5["mtm_max_drawdown_fraction"],
                      "funnel": {k: r5.get(k) for k in ("pa_evaluations", "id_approvals", "mpc_plans", "safety_approvals",
                                                        "gates_passed", "orders_submitted", "fills",
                                                        "plant_entry_rejections", "portfolio_cap_rejections",
                                                        "execution_halted")}},
        "reconciliation": {"breaks": [], "note": "Engine A has no independent broker; _finalize_run asserts paper "
                                                 "broker gross == trade-ledger gross"},
        "trades": trades,
        "r5_authority": engine.authority_report_section(r5),
        "source": inputs["source"],
        "data_audit": inputs["audit"],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, default=str))
    print(f"ENGINE A (paper broker reference)  trades {len(trades)}  gross {r5['gross_pnl']:.2f}  "
          f"net {r5['net_pnl']:.2f}  report {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
