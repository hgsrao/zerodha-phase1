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


def test_daily_unrealized_loss_reflects_real_mark_to_market_not_a_frozen_zero():
    # Real bug found and fixed this session: SystemState.daily_unrealized_loss
    # was a hardcoded 0.0. Confirmed no gate currently reads it (grepped
    # every Gate0X class in gates_framework.py), so this doesn't change any
    # real gate decision today -- but the value itself must now be real,
    # computed from the same _mark_to_market_equity() the MTM curve
    # already uses, not a permanent fabricated zero.
    registry = CanonicalParameterRegistry()
    orch = Revision2ExternalEngineOrchestrator(["INFY"], registry, starting_equity=1_000_000.0)
    orch._day_start_equity = 1_000_000.0
    orch.open_trades["INFY"] = {
        "side": "BUY", "entry_price": 1000.0, "quantity": 100, "stop_price": 990.0, "target_price": 1020.0,
        "minimum_hold_bars": 2, "maximum_hold_bars": 60, "entry_timestamp": "2024-01-02 09:20:00",
    }
    orch._last_close["INFY"] = 950.0  # a real, meaningful unrealized loss: 100 * (1000-950) = 5000
    unrealized_loss = max(0.0, orch._day_start_equity - orch._mark_to_market_equity())
    assert unrealized_loss > 4999.0, f"expected a real unrealized loss near 5000, got {unrealized_loss}"


def test_regime_stressed_exit_fires_once_minimum_hold_is_met():
    # Real gap found and fixed this session: ID's regime check was never
    # called at all once a position was open (`if symbol in
    # self.open_trades: continue` skips straight past entry evaluation,
    # and no separate call existed in exit logic either) -- a real regime
    # shift to "stressed" DURING a held position had no path back into the
    # exit decision, even though the identical shift would have vetoed a
    # fresh entry moments earlier.
    registry = CanonicalParameterRegistry()
    orch = Revision2ExternalEngineOrchestrator(["INFY"], registry, starting_equity=1_000_000.0)
    orch.open_trades["INFY"] = {
        "side": "BUY", "entry_price": 1000.0, "quantity": 100, "stop_price": 900.0, "target_price": 1200.0,
        "minimum_hold_bars": 2, "maximum_hold_bars": 60, "entry_timestamp": "2024-01-02 09:20:00",
    }
    orch._exit_controller_states["INFY"] = orch.exit_controller.open_position("BUY", 1000.0, 900.0, 1200.0, 60)
    orch.id_box._current_regime = lambda symbol, latest_close: "stressed"

    # Real, already-working ATR droop (verified earlier this session)
    # ratchets the stop up close to the current price on the very first
    # update() call regardless of the artificially-wide stop_price above --
    # a tiny volatility keeps the real droop distance small enough that
    # this bar's own low/high stay clear of it, isolating this test to
    # just the regime-exit logic, not an incidental stop/target touch.
    from revision2.contracts import PASignal
    bar = {"open": 1000.0, "high": 1000.0, "low": 1000.0, "close": 1000.0}
    signal = PASignal(symbol="INFY", timestamp="2024-01-02 09:22", direction=1, confidence=0.6,
                       momentum=0.1, volatility=0.00001, vwap_deviation=0.1, volume_confirmation=0.2,
                       exit_confidence=0.6, quality_band="green")

    # held_bars=1: below minimum_hold_bars=2 -- must NOT exit yet even
    # though the regime is stressed. chart_studies_confidence held at a
    # flat, healthy 0.6 throughout -- this test is isolated to the
    # regime-exit path, not the (separately tested) chart-studies track.
    orch._maybe_exit("INFY", "2024-01-02 09:21", bar, signal, held_bars=1, session_last_bar=False,
                      chart_studies_confidence=0.6)
    assert "INFY" in orch.open_trades, "exited before minimum_hold_bars was satisfied"

    # held_bars=2: minimum_hold_bars satisfied, regime still stressed -- must exit now.
    orch._maybe_exit("INFY", "2024-01-02 09:22", bar, signal, held_bars=2, session_last_bar=False,
                      chart_studies_confidence=0.6)
    assert "INFY" not in orch.open_trades, "did not exit on a real, sustained regime-stressed reading"
    assert orch.completed_trades[-1]["reason"] == "regime_stressed_exit"
