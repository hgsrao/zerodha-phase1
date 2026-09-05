import sys
sys.path.insert(0, ".")

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator


def _real_bars(symbols, tail=1500):
    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    return {s: loader._load_symbol_csv(s).tail(tail).reset_index(drop=True) for s in symbols}


def test_full_engine_runs_on_real_data_and_produces_real_trades():
    symbols = ["ADANIENT", "INFY", "TCS"]
    bars = _real_bars(symbols)
    registry = CanonicalParameterRegistry()

    orch = Revision2ExternalEngineOrchestrator(symbols, registry, starting_equity=1_000_000.0)
    assert orch.startup_certificate.passed

    report = orch.run(bars, warmup=60)

    assert report["bars_processed"] > 1000
    assert report["completed_trades"] > 0, "engine produced zero trades -- proves nothing"
    # Ledger reconciliation: broker.realized_pnl must equal the sum of
    # completed trades' pnl (enforced by an internal assert in run() too,
    # but checked again here from the caller's side of the report).
    assert abs(report["gross_pnl"] - sum(t["pnl"] for t in report["trades"])) < 1e-6
    assert abs(report["net_pnl"] - sum(t["net_pnl"] for t in report["trades"])) < 1e-6

    # Every expected box actually ran and left a trace.
    assert report["pa_signals"] > 0
    assert report["id_approvals"] > 0
    assert report["mpc_plans"] > 0

    coverage = report["parameter_coverage"]
    # 5, not 6 -- see orchestrator.py and position_sizing_pyportfolioopt.py's
    # own module docstrings for why each is genuinely not consumed by this
    # engine. max_sector_exposure_fraction used to be here too under a
    # "replaced by PyPortfolioOpt weights" rationale that turned out to be
    # false: an external review found it was actually being read (and a
    # real sector cap enforced with it) via self.registry.get(...).default,
    # bypassing self.config -- always the frozen default, never a
    # calibration override, and invisible to consumption tracking as a
    # side effect of that bypass. Fixed to read self.config.require(...)
    # like every other consumed parameter; it now shows up as consumed,
    # correctly, because it always was.
    expected_missing = {
        "data_validation_mode",  # Pandera certification has no strict/lenient mode toggle
        "learning_rate_exploration_factor", "phase1_exploration_intensity", "phase2_optimization_intensity",
        "max_symbol_concentration",  # replaced by PyPortfolioOpt weights
    }
    assert set(coverage["target_missing"]) == expected_missing, (
        f"unexpected parameter coverage gap: {set(coverage['target_missing']) - expected_missing}"
    )


def test_precomputed_clock_produces_identical_results_to_building_it_internally():
    # Calibration drives many candidates against the SAME symbol_bars, only
    # the parameters change -- so the (potentially multi-million-event)
    # clock can be built once and reused, mirroring
    # revision2/portfolio_orchestrator.py's identical optimization. Must be
    # byte-identical, not just "close", or reusing the clock would be
    # silently changing what calibration actually measures.
    symbols = ["ADANIENT", "INFY", "TCS"]
    bars = _real_bars(symbols)
    registry = CanonicalParameterRegistry()

    orch_internal = Revision2ExternalEngineOrchestrator(symbols, registry, starting_equity=1_000_000.0)
    report_internal = orch_internal.run(bars, warmup=60)

    orch_precomputed = Revision2ExternalEngineOrchestrator(symbols, registry, starting_equity=1_000_000.0)
    clock = Revision2ExternalEngineOrchestrator.build_clock(bars, 60)
    report_precomputed = orch_precomputed.run(bars, warmup=60, precomputed_clock=clock)

    assert report_internal["completed_trades"] == report_precomputed["completed_trades"]
    assert report_internal["completed_trades"] > 0, "precondition: this fixture must produce real trades"
    assert report_internal["net_pnl"] == report_precomputed["net_pnl"]
    assert report_internal["trades"] == report_precomputed["trades"]


def test_startup_certification_rejects_an_invalid_override():
    registry = CanonicalParameterRegistry()
    import pytest
    with pytest.raises(ValueError):
        Revision2ExternalEngineOrchestrator(
            ["INFY"], registry, calibration_overrides={"momentum_weight": 999.0},
        )
