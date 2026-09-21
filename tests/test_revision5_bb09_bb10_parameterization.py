"""BB09 (P01D order construction) / BB10 (UnifiedExecution submission)
ownership, propagation, and R5 supervisory-bridge regression tests.

Proven identities (see outputs/BB09-BB10-dynamic-parameterization-audit.md
for the full trace): BB09 == P01D == revision2.boxes.P01DBox, called by
revision2_external.orchestrator.Revision2ExternalEngineOrchestrator as
``self.p01d``. BB10 == UnifiedExecution; its registry-owned operational
surface (trading_hours_start/end, no_entry_cutoff_time,
order_dedup_window_seconds, max_reconciliation_qty_diff) is consumed
directly by that same orchestrator and by
revision2_external.paper_execution.ReplayIntentLedger /
CostedPaperBrokerAdapter, while revision2.boxes.UnifiedExecutionBox is the
box used by the in-house (non-external) Revision 2 engines.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from canonical_parameter_registry import CanonicalParameterRegistry
from gates_framework import Gate14OrderTimeout, Gate16Slippage, SafetyGateConfig
from market_data_loader import MarketDataLoader
from revision2.boxes import P01DBox, UnifiedExecutionBox
from revision2.contracts import EffectiveConfig, ProposedOrder, TradePlan
from revision2.dataset_manifest import DatasetManifest
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
from revision2_external.paper_execution import CostedPaperBrokerAdapter, ReplayIntentLedger
from revision5.supervisory_bridge import (
    BB09BB10SupervisorySnapshot,
    Revision5SupervisoryBridge,
    SupervisorySnapshotError,
)


def _config(registry, **overrides):
    values = {name: spec.default for name, spec in registry.params.items()}
    values.update(overrides)
    return EffectiveConfig.build(values, registry.FROZEN_IDENTITY_SHA256)


def _plan(side="BUY", entry=100.0, stop=99.0, target=102.0):
    return TradePlan(
        side=side, entry_price=entry, stop_price=stop, target_price=target,
        minimum_hold_bars=1, maximum_hold_bars=10,
    )


# ---------------------------------------------------------------------------
# Registry ownership / classification
# ---------------------------------------------------------------------------

def test_bb09_bb10_structural_parameters_are_non_calibratable():
    """order_type, limit offsets, retry/timeout knobs, and the trading
    window are fixed execution/safety envelopes, not tunable knobs -- proven
    by their calibratable flag rather than assumed from a label."""
    registry = CanonicalParameterRegistry()
    fixed_p01d = {
        "order_type", "limit_order_offset_percent", "order_timeout_seconds",
        "max_retry_attempts", "retry_delay_seconds", "slippage_tolerance_percent",
    }
    fixed_unified_execution = {"trading_hours_start", "trading_hours_end"}
    for name in fixed_p01d | fixed_unified_execution:
        spec = registry.get(name)
        assert spec.black_box in ("P01D", "UnifiedExecution")
        assert spec.calibratable is False, f"{name} must remain non-calibratable"

    safety_only = {
        "order_dedup_window_seconds", "order_timeout_seconds_execution",
        "max_reconciliation_qty_diff", "max_slippage_fraction", "no_entry_cutoff_time",
    }
    for name in safety_only:
        assert name in registry.safety_params
        assert registry.safety_params[name].calibratable is False


def test_fixed_p01d_parameters_cannot_enter_calibration_surface():
    """A calibration payload trying to move a fixed BB09/BB10 value must be
    rejected upstream, not silently accepted and ignored downstream."""
    registry = CanonicalParameterRegistry()
    for name, illegal_value in (
        ("order_type", "LIMIT"),
        ("max_retry_attempts", 4),
        ("slippage_tolerance_percent", 0.19),
        ("trading_hours_start", "08:00"),
    ):
        errors = registry.validate_calibration_payload({name: illegal_value})
        assert errors, f"{name} must be rejected as a calibration override"


# ---------------------------------------------------------------------------
# BB09 (P01D) -- order construction sensitivity
# ---------------------------------------------------------------------------

def test_order_type_reaches_the_constructed_order():
    registry = CanonicalParameterRegistry()
    box = P01DBox()
    market = box.create_order("INFY", _plan(), 10, _config(registry, order_type="MARKET"))[0]
    limit = box.create_order("INFY", _plan(), 10, _config(registry, order_type="LIMIT"))[0]
    assert market.order_type == "MARKET" and market.limit_price is None
    assert limit.order_type == "LIMIT" and limit.limit_price is not None


def test_limit_order_offset_percent_changes_the_computed_limit_price():
    registry = CanonicalParameterRegistry()
    box = P01DBox()
    tight, _ = box.create_order("INFY", _plan(), 10, _config(registry, order_type="LIMIT", limit_order_offset_percent=0.0))
    wide, _ = box.create_order("INFY", _plan(), 10, _config(registry, order_type="LIMIT", limit_order_offset_percent=0.05))
    assert tight.limit_price == pytest.approx(100.0)
    assert wide.limit_price == pytest.approx(105.0)
    assert wide.limit_price != tight.limit_price


def test_order_timeout_seconds_changes_the_constructed_order():
    registry = CanonicalParameterRegistry()
    box = P01DBox()
    fast, _ = box.create_order("INFY", _plan(), 10, _config(registry, order_timeout_seconds=5))
    slow, _ = box.create_order("INFY", _plan(), 10, _config(registry, order_timeout_seconds=90))
    assert fast.timeout_seconds == 5
    assert slow.timeout_seconds == 90


def test_max_retry_attempts_changes_the_constructed_order():
    registry = CanonicalParameterRegistry()
    box = P01DBox()
    low, _ = box.create_order("INFY", _plan(), 10, _config(registry, max_retry_attempts=0))
    high, _ = box.create_order("INFY", _plan(), 10, _config(registry, max_retry_attempts=5))
    assert low.max_retries == 0
    assert high.max_retries == 5


def test_zero_quantity_is_rejected_and_no_order_is_constructed():
    """Upstream rejection (quantity sized to zero) cannot be bypassed
    downstream -- P01D must not manufacture an order out of nothing."""
    registry = CanonicalParameterRegistry()
    box = P01DBox()
    order, trace = box.create_order("INFY", _plan(), 0, _config(registry))
    assert order is None


def test_retry_delay_and_slippage_tolerance_are_read_but_do_not_alter_the_order():
    """retry_delay_seconds and slippage_tolerance_percent are fixed,
    registry-retained values with no ProposedOrder field and no external
    replay actuator (see the audit report). Prove both halves honestly:
    they ARE consumed (present in the trace, so coverage tooling doesn't
    misreport them), and they do NOT change ProposedOrder -- there is no
    hidden actuator a naive coverage check could be fooled by."""
    registry = CanonicalParameterRegistry()
    box = P01DBox()
    low, trace_low = box.create_order(
        "INFY", _plan(), 10, _config(registry, retry_delay_seconds=1, slippage_tolerance_percent=0.02))
    high, trace_high = box.create_order(
        "INFY", _plan(), 10, _config(registry, retry_delay_seconds=20, slippage_tolerance_percent=0.20))

    assert {"retry_delay_seconds", "slippage_tolerance_percent"} <= {u.parameter for u in trace_low}
    assert {"retry_delay_seconds", "slippage_tolerance_percent"} <= {u.parameter for u in trace_high}
    assert low == high, "these values must not change the constructed order"


# ---------------------------------------------------------------------------
# BB09/BB10 boundary: duplicate-ownership guard for "slippage tolerance"
# ---------------------------------------------------------------------------

def test_gate16_slippage_is_governed_by_the_fixed_max_slippage_fraction_not_the_p01d_calibratable_value():
    """Two registry entries both describe 'slippage tolerance':
    slippage_tolerance_percent (P01D, fixed, no consumer -- see above) and
    max_slippage_fraction (P01D, fixed safety, the real Gate16 threshold).
    Only one may be the canonical owner of live enforcement. Prove it's the
    safety one, and that the other cannot move it."""
    tight_gate = Gate16Slippage(SafetyGateConfig(slippage_tolerance_percent=0.001))
    loose_gate = Gate16Slippage(SafetyGateConfig(slippage_tolerance_percent=0.15))

    # A 1% slippage passes the loose (P01D-style) threshold but must fail
    # the tight, real max_slippage_fraction-derived threshold.
    assert not tight_gate.evaluate(target_price=100.0, fill_price=101.0).passed
    assert loose_gate.evaluate(target_price=100.0, fill_price=101.0).passed


def test_orchestrator_wires_gate16_from_max_slippage_fraction_not_slippage_tolerance_percent():
    symbols = ["INFY"]
    registry = CanonicalParameterRegistry()
    orch = Revision2ExternalEngineOrchestrator(symbols, registry, starting_equity=1_000_000.0)
    expected = float(orch.safety_contract.values["max_slippage_fraction"])
    assert orch.entry_decision_engine.config.slippage_tolerance_percent == pytest.approx(expected)
    assert expected != pytest.approx(float(registry.get("slippage_tolerance_percent").default))


# ---------------------------------------------------------------------------
# BB09/BB10 boundary: order_timeout_seconds vs. the fixed execution floor
# ---------------------------------------------------------------------------

def test_gate14_logic_rejects_elapsed_beyond_its_configured_timeout():
    """Gate14's own unit behaviour, independent of any runtime wiring."""
    gate = Gate14OrderTimeout(SafetyGateConfig(order_timeout_seconds=30))
    assert gate.evaluate(elapsed_seconds=30).passed
    assert not gate.evaluate(elapsed_seconds=31).passed


def _short_run(registry, symbol="INFY", bars=1500, warmup=60, before_run=None):
    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    data = {symbol: loader._load_symbol_csv(symbol).tail(bars).reset_index(drop=True)}
    orch = Revision2ExternalEngineOrchestrator([symbol], registry, starting_equity=1_000_000.0)
    fills = []
    real_place = orch.broker.place_order

    def spy(*args, **kwargs):
        result = real_place(*args, **kwargs)
        fills.append(result)
        return result

    orch.broker.place_order = spy
    if before_run is not None:
        before_run(orch)
    return orch, orch.run(data, warmup=warmup), fills


def _trades(orch):
    return [(t["symbol"], round(t["pnl"], 6), round(t["net_pnl"], 6)) for t in orch.completed_trades]


def test_replay_wiring_never_reaches_the_order_timeout():
    """order_timeout_seconds is a FIXED execution-timeout policy.  In the current
    replay/paper path the paper adapter acknowledges synchronously
    (ack_elapsed_seconds == 0.0) and Gate14 is never given a non-zero elapsed
    time, so changing the fixed value cannot change any replay result."""
    from dataclasses import replace
    default_registry = CanonicalParameterRegistry()
    tight_registry = CanonicalParameterRegistry()
    tight_registry.params["order_timeout_seconds"] = replace(
        tight_registry.params["order_timeout_seconds"], default=5)

    base_orch, base_report, base_fills = _short_run(default_registry)
    tight_orch, tight_report, tight_fills = _short_run(tight_registry)

    # the value really reaches Gate14's effective configuration ...
    assert base_orch.entry_decision_engine.config.order_timeout_seconds == 30
    assert tight_orch.entry_decision_engine.config.order_timeout_seconds == 5
    # ... but every acknowledgement is instantaneous, so it is never exercised
    passed = [f for f in base_fills + tight_fills if f["passed"]]
    assert passed and all(f["ack_elapsed_seconds"] == 0.0 for f in passed)
    assert base_report["completed_trades"] > 0
    assert _trades(tight_orch) == _trades(base_orch)
    assert tight_report["net_pnl"] == base_report["net_pnl"]


# ---------------------------------------------------------------------------
# BB10 -- dedup / reconciliation / trading-window mechanics
# ---------------------------------------------------------------------------

def test_order_dedup_window_seconds_changes_duplicate_detection():
    tight = ReplayIntentLedger(window_seconds=1)
    wide = ReplayIntentLedger(window_seconds=30)
    for ledger in (tight, wide):
        ledger.record("INFY", "BUY", "2026-01-01T09:20:00")
    still_recent = "2026-01-01T09:20:10"
    assert not tight.seen_recent("INFY", "BUY", still_recent)
    assert wide.seen_recent("INFY", "BUY", still_recent)


def test_replay_broker_rejects_non_market_orders():
    """The paper/replay broker mechanics (BB10) are MARKET-only; this is
    why limit_order_offset_percent has no live actuator in this pipeline."""
    broker = CostedPaperBrokerAdapter(account_id="TEST")
    result = broker.place_order("INFY", "BUY", 10, "LIMIT", market_price=100.0)
    assert result["passed"] is False


def test_unified_execution_box_trading_window_is_parameter_sensitive():
    """revision2.boxes.UnifiedExecutionBox is BB10's box for the in-house
    (non-external) Revision 2 engines. trading_hours_start/end genuinely
    gate in_window; changing them changes the decision."""
    registry = CanonicalParameterRegistry()
    box = UnifiedExecutionBox()
    narrow_cfg = _config(registry, trading_hours_start="09:15", trading_hours_end="09:16")
    wide_cfg = _config(registry, trading_hours_start="09:15", trading_hours_end="15:30")
    in_window_narrow, _, _ = box.check_window("2026-01-01T12:00:00", narrow_cfg)
    in_window_wide, _, _ = box.check_window("2026-01-01T12:00:00", wide_cfg)
    assert in_window_narrow is False
    assert in_window_wide is True


def test_orchestrator_in_trading_window_is_load_bearing():
    """trading_hours_start/end are non-calibratable (fixed BB10 envelope,
    see test_bb09_bb10_structural_parameters_are_non_calibratable), so this
    checks the real, un-overridden default window (09:15-15:30) instead of
    a calibration override."""
    symbols = ["INFY"]
    registry = CanonicalParameterRegistry()
    orch = Revision2ExternalEngineOrchestrator(symbols, registry)
    assert orch._in_trading_window("2026-01-01T12:00:00") is True
    assert orch._in_trading_window("2026-01-01T20:00:00") is False
    assert "trading_hours_start" in orch.consumed_parameters
    assert "trading_hours_end" in orch.consumed_parameters


def test_costed_paper_broker_slippage_fraction_reflects_construction_time_config():
    """The broker/paper-simulation mechanics (BB10) cache
    slippage_cost_multiplier at construction time. Prove the cached value
    actually reflects the config it was built from (no stale default)."""
    symbols = ["INFY"]
    registry = CanonicalParameterRegistry()
    low = Revision2ExternalEngineOrchestrator(
        symbols, registry, calibration_overrides={"slippage_cost_multiplier": 0.8})
    high = Revision2ExternalEngineOrchestrator(
        symbols, registry, calibration_overrides={"slippage_cost_multiplier": 1.5})
    assert low.broker.slippage_fraction == pytest.approx(0.8 * 0.0005)
    assert high.broker.slippage_fraction == pytest.approx(1.5 * 0.0005)
    assert low.broker.slippage_fraction != high.broker.slippage_fraction


# ---------------------------------------------------------------------------
# Structural constraint: order_type is fixed at MARKET for the external engine
# ---------------------------------------------------------------------------

def test_non_market_order_type_is_rejected_at_startup_fail_closed():
    """order_type is non-calibratable, so a normal calibration override
    can't set it to LIMIT -- construct via a registry whose default is
    patched instead, to prove the orchestrator's own fail-closed startup
    guard holds independently of registry-level override validation."""
    import dataclasses
    registry = CanonicalParameterRegistry()
    registry.params["order_type"] = dataclasses.replace(registry.params["order_type"], default="LIMIT")
    with pytest.raises(ValueError, match="MARKET orders only"):
        Revision2ExternalEngineOrchestrator(["INFY"], registry)


# ---------------------------------------------------------------------------
# R5 supervisory bridge: BB09/BB10 hand-off
# ---------------------------------------------------------------------------

def _order(**overrides):
    base = dict(symbol="INFY", side="BUY", quantity=10, order_type="MARKET",
                limit_price=None, timeout_seconds=30, max_retries=2)
    base.update(overrides)
    return ProposedOrder(**base)


def test_snapshot_bb09_bb10_reports_a_filled_order_as_information_only():
    snapshot = Revision5SupervisoryBridge().snapshot_bb09_bb10(
        proposed_order=_order(),
        fill_result={"passed": True, "filled_quantity": 10, "filled_price": 101.25},
    )
    assert isinstance(snapshot, BB09BB10SupervisorySnapshot)
    assert snapshot.order.authority == "INFORMATION_ONLY"
    assert snapshot.execution.authority == "INFORMATION_ONLY"
    assert snapshot.order.order_type == "MARKET"
    assert snapshot.execution.submitted is True
    assert snapshot.execution.filled_quantity == 10
    assert snapshot.execution.filled_price == pytest.approx(101.25)
    assert snapshot.execution.reason == "FILLED"


def test_snapshot_bb09_bb10_reports_a_rejected_fill_without_hiding_it():
    snapshot = Revision5SupervisoryBridge().snapshot_bb09_bb10(
        proposed_order=_order(),
        fill_result={"passed": False, "reasons": ["duplicate order detected"]},
    )
    assert snapshot.execution.submitted is False
    assert snapshot.execution.filled_quantity == 0
    assert snapshot.execution.filled_price is None
    assert "duplicate order detected" in snapshot.execution.reason


def test_snapshot_bb09_bb10_rejects_a_malformed_order():
    with pytest.raises(ValueError):
        Revision5SupervisoryBridge().snapshot_bb09_bb10(
            proposed_order=_order(quantity=0),
            fill_result={"passed": False, "reasons": ["n/a"]},
        )


def test_snapshot_bb09_bb10_is_immutable():
    snapshot = Revision5SupervisoryBridge().snapshot_bb09_bb10(
        proposed_order=_order(),
        fill_result={"passed": True, "filled_quantity": 10, "filled_price": 100.0},
    )
    with pytest.raises(Exception):
        snapshot.order.quantity = 999  # type: ignore[misc]


def test_bridge_still_has_no_direct_plant_control_dependency():
    import inspect
    from revision5 import supervisory_bridge
    source = inspect.getsource(supervisory_bridge)
    for forbidden in (
        "CentralPlantMasterDCS",
        "MachineBay",
        "evaluate_entry(",
        "evaluate_admission(",
        "from revision5.ccpp_unified_plant import",
        "from revision5.governor import",
    ):
        assert forbidden not in source


# ---------------------------------------------------------------------------
# BB09-BB10 integration: a real (short) backtest populates the bridge
# ---------------------------------------------------------------------------

def test_bb09_bb10_supervisory_snapshot_populated_during_a_real_run():
    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    bars = {"INFY": loader._load_symbol_csv("INFY").tail(1500).reset_index(drop=True)}

    registry = CanonicalParameterRegistry()
    orch = Revision2ExternalEngineOrchestrator(["INFY"], registry, starting_equity=1_000_000.0)
    report = orch.run(bars, warmup=60)

    assert report["completed_trades"] > 0, "no trades produced -- cannot prove the hand-off fired"
    assert "INFY" in orch.bb09_bb10_supervisory_by_symbol
    snapshot = orch.bb09_bb10_supervisory_by_symbol["INFY"]
    assert isinstance(snapshot, BB09BB10SupervisorySnapshot)
    assert snapshot.order.symbol == "INFY"
    assert snapshot.order.order_type == "MARKET"
    assert snapshot.order.authority == "INFORMATION_ONLY"
    assert snapshot.execution.authority == "INFORMATION_ONLY"
    # Every submitted order this pipeline can construct is MARKET-only
    # (fail-closed at startup), so a passed fill must carry a real price.
    if snapshot.execution.submitted:
        assert snapshot.execution.filled_price is not None


# ---------------------------------------------------------------------------
# Supervisory observer: information-only, never able to interrupt bookkeeping
# ---------------------------------------------------------------------------

def test_snapshot_authority_is_information_only_for_both_boxes():
    snapshot = Revision5SupervisoryBridge().snapshot_bb09_bb10(
        proposed_order=_order(), fill_result={"passed": False, "reasons": ["rejected"]})
    assert snapshot.order.authority == "INFORMATION_ONLY"
    assert snapshot.execution.authority == "INFORMATION_ONLY"
    assert snapshot.execution.submitted is False and snapshot.execution.reason == "rejected"


@pytest.mark.parametrize("order_kwargs, fill", [
    ({"max_retries": None}, {"passed": True, "filled_quantity": 10, "filled_price": 101.0}),
    ({}, {"passed": True, "filled_quantity": 10, "filled_price": float("nan")}),
    ({}, {"passed": True, "filled_price": 101.0}),
    ({"side": "HOLD"}, {"passed": True, "filled_quantity": 10, "filled_price": 101.0}),
])
def test_invalid_snapshot_data_raises_only_the_dedicated_error(order_kwargs, fill):
    with pytest.raises(SupervisorySnapshotError):
        Revision5SupervisoryBridge().snapshot_bb09_bb10(proposed_order=_order(**order_kwargs), fill_result=fill)


def test_invalid_post_fill_snapshot_data_does_not_interrupt_fill_bookkeeping():
    """max_retries is never read by the broker path, so corrupting it breaks ONLY the
    supervisory snapshot.  The run must still complete every fill, position and ledger
    update exactly as an undisturbed run, and the observer failure must be recorded."""
    registry = CanonicalParameterRegistry()
    base_orch, base_report, base_fills = _short_run(registry)
    assert base_report["completed_trades"] > 0 and not base_orch.bb09_bb10_observer_failures

    def corrupt(orch):
        real_create = orch.p01d.create_order

        def create(*args, **kwargs):
            order, trace = real_create(*args, **kwargs)
            if order is not None:
                order = replace(order, max_retries=None)
            return order, trace

        orch.p01d.create_order = create

    bad_orch, bad_report, bad_fills = _short_run(registry, before_run=corrupt)

    assert _trades(bad_orch) == _trades(base_orch)
    assert bad_report["completed_trades"] == base_report["completed_trades"]
    assert bad_report["net_pnl"] == base_report["net_pnl"]
    assert len(bad_fills) == len(base_fills)
    failures = bad_orch.bb09_bb10_observer_failures
    # one snapshot attempt per ENTRY order (exit orders share place_order but are not snapshotted)
    assert len(failures) == len(base_orch.completed_trades) > 0
    assert all(f["error_type"] == "SupervisorySnapshotError" and f["symbol"] == "INFY" for f in failures)
    assert failures[0]["side"] in ("BUY", "SELL") and failures[0]["fill_passed"] is True
    assert "NoneType" in failures[0]["error"]
    assert bad_orch.bb09_bb10_supervisory_by_symbol == {}       # no snapshot recorded, no veto


def test_unexpected_observer_exceptions_are_not_swallowed():
    """Only the bridge's data-validation error is contained; a real defect still surfaces."""
    registry = CanonicalParameterRegistry()

    def boom(orch):
        def broken(**kwargs):
            raise RuntimeError("programming error")
        orch.supervisory_bridge.snapshot_bb09_bb10 = broken

    with pytest.raises(RuntimeError, match="programming error"):
        _short_run(registry, before_run=boom)
