"""Real-data, fail-closed SUNPHARMA sealed paper replay for the in-house engine."""

import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from inhouse_validation.cross_session_rejection import CrossSessionRejectionPolicy
from inhouse_validation.gate16_remediation import Gate16Remediator
from inhouse_validation.manifest_loader import ManifestLoader
from revision2.portfolio_orchestrator import Revision2PortfolioOrchestrator


MANIFEST = "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
SYMBOL = "SUNPHARMA"
WARMUP = 60


def run():
    loader = ManifestLoader(MANIFEST)
    entry = loader.manifest.get_file(SYMBOL)
    if entry is None:
        raise RuntimeError(f"{SYMBOL} is not manifest admitted")
    data_dir = Path(os.environ.get("NSE_DATA_DIR", loader.manifest.data_dir))
    path = data_dir / entry.filename
    actual = loader._compute_file_hash(path)
    if actual != entry.sha256:
        raise RuntimeError(f"{SYMBOL} hash mismatch")
    bars = pd.read_csv(path)
    if len(bars) <= WARMUP:
        raise RuntimeError(f"{SYMBOL} has insufficient warmup bars")

    registry = CanonicalParameterRegistry()
    values = {name: spec.default for name, spec in registry.params.items()}
    config_hash = hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()
    audit_path = Path("diagnostic_output/inhouse_sunpharma_gate16_audit.jsonl")
    orchestrator = Revision2PortfolioOrchestrator([SYMBOL], registry=registry, starting_equity=100_000.0)
    orchestrator.cross_session_policy = CrossSessionRejectionPolicy(allow_cross_session=False)
    orchestrator.gate16_remediator = Gate16Remediator(0.15, run_id="inhouse-sunpharma-sealed", audit_log_path=audit_path)
    result = orchestrator.run({SYMBOL: bars}, warmup=WARMUP)

    daily = defaultdict(float)
    for trade in result["trades"]:
        daily[pd.Timestamp(trade["exit_timestamp"]).date().isoformat()] += float(trade["net_pnl"])
    exact = not orchestrator.open_trades and not orchestrator.pending_entries
    if not exact or not orchestrator.gate16_remediator.verify_chain():
        raise RuntimeError("sealed replay reconciliation or audit integrity failed")
    if result["completed_trades"] == 0:
        status = "NO_EXECUTION"
    elif orchestrator.gate16_remediator.violations:
        status = "REMEDIATION_REQUIRED"
    else:
        status = result["status"]
    report = {"status": status, "symbol": SYMBOL, "dataset_hash": loader.get_dataset_hash(),
              "file_hash": actual, "config_hash": config_hash, "warmup_bars": WARMUP,
              "metrics": {k: result[k] for k in ("bars_processed", "orders_submitted", "fills", "completed_trades", "net_pnl", "total_cost")},
              "rejection_funnel": {
                  **{k: result.get(k, 0) for k in (
                      "pa_signals", "id_approvals", "id_rejections", "mpc_plans",
                      "safety_approvals", "safety_rejections", "gates_evaluated",
                      "gates_passed", "gates_rejected", "orders_queued",
                      "portfolio_cap_rejections", "cross_session_rejections",
                      "pending_orders_cancelled",
                  )},
                  "safety_rejection_reasons": result.get("safety_rejection_reasons", {}),
              },
              "daily_net_pnl": dict(sorted(daily.items())), "reconciliation_exact": exact,
              "event_ledger": result["event_ledger"], "gate16_violations": len(orchestrator.gate16_remediator.violations),
              "audit_chain_valid": orchestrator.gate16_remediator.verify_chain(), "audit_path": str(audit_path)}
    Path("diagnostic_output").mkdir(exist_ok=True)
    Path("diagnostic_output/inhouse_sunpharma_sealed_report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps({"status": status, "trades": result["completed_trades"], "reconciliation": exact}, indent=2))
    return report


if __name__ == "__main__":
    run()
