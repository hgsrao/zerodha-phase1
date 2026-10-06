"""C04-A: compose already-verified controller codecs into real morning recovery.

Scope:
- ID/HMM state
- sparse per-symbol HMM risk hysteresis
- causal outcome ledger

This deliberately does NOT certify execution-cursor resume.  Admissions must
remain blocked after preparation.
"""
import json

import pandas as pd
import pytest

from revision2_external.closed_loop_control import HMMRiskHysteresis
from revision5.state_recovery import StateRecoveryJournal
from tests.test_r5_morning_startup import boot, carry, recovered_broker


def _json(value):
    """Exercise the same JSON-safe boundary expected of durable boot state."""
    return json.loads(json.dumps(value, allow_nan=False))


def _populate_c04a(first):
    # ID: carry() has already initialized TITAN through the real calibration
    # API.  Advance it with real positive closes without fabricating a model.
    for close in (110.0, 111.0, 109.5, 112.0):
        first.id_box._current_regime("TITAN", close)

    # Sparse hysteresis: only TITAN exists in the map.  This specifically
    # proves morning recovery does not require one controller per universe
    # symbol.
    hysteresis = HMMRiskHysteresis()
    hysteresis.configure(first.config)
    for _ in range(20):
        hysteresis.update(
            {"available": True, "stress_probability": 0.99}
        )
    assert hysteresis.stressed_latched
    first._hmm_risk_hysteresis["TITAN"] = hysteresis

    # Real causal outcome ledger API.
    first.closed_loop.outcomes.record(
        {
            "symbol": "TITAN",
            "side": "BUY",
            "regime": "calm",
            "net_pnl": 125.0,
            "trade_id": "c04a-outcome-1",
            "metadata": {"source": "c04a-composition"},
        }
    )


def test_c04a_checkpoint_restart_restores_composed_controller_state(tmp_path):
    first, runtime, store, truth = carry(tmp_path)

    _populate_c04a(first)

    # Append a new durable checkpoint containing populated C04-A state.
    first._checkpoint_morning_recovery(
        account_id="fixture-account",
        timestamp=pd.Timestamp("2023-12-05 15:26"),
    )

    journal = StateRecoveryJournal(store.connection)
    saved = journal.load_boot()

    assert "id_v1" in saved
    assert "hmm_hysteresis_v1" in saved
    assert "outcomes_v1" in saved

    expected_id = _json(saved["id_v1"])
    expected_hysteresis = _json(saved["hmm_hysteresis_v1"])
    expected_outcomes = _json(saved["outcomes_v1"])

    # Non-vacuous persisted state.
    assert expected_id["symbols"]["TITAN"]["history"]["values"]
    assert set(expected_hysteresis) == {"TITAN"}
    assert expected_hysteresis["TITAN"]["state"]["stressed_latched"] is True
    assert len(expected_outcomes["outcomes"]) == 1

    store.close()

    second, runtime2, store2 = boot(tmp_path)

    # Fresh process-style controller state before preparation.
    assert second.id_box._bar_history == {}
    assert second._hmm_risk_hysteresis == {}
    assert second.closed_loop.outcomes._outcomes == []

    receipt = second._reconcile_morning_startup(
        account_id="fixture-account",
        broker=recovered_broker(truth),
    )

    assert receipt["prepared"]
    assert not receipt["admissions_allowed"]
    assert second._execution_halted
    assert second._morning_recovery_prepared

    # Exact durable identity after composition.
    assert _json(second.id_box.export_state(
        second.config, second.symbols
    )) == expected_id

    actual_hysteresis = {
        symbol: controller.export_state(second.config)
        for symbol, controller in second._hmm_risk_hysteresis.items()
    }
    assert _json(actual_hysteresis) == expected_hysteresis

    assert _json(second.closed_loop.outcomes.export_state(
        second.config, second.symbols
    )) == expected_outcomes

    # C04-B is intentionally still open: prepared state must not be allowed
    # to enter fresh-input replay without a certified resume cursor.
    with pytest.raises(RuntimeError, match="resume cursor"):
        second.run({})

    store2.close()


def _fresh_controller_snapshot(engine):
    """Controller identities/state that must survive a refused preparation."""
    return {
        # prepare() stages replacement objects and installs them only at the
        # final commit boundary.  Identity therefore directly detects a
        # partial install without depending on private codec implementation.
        "id_object": id(engine.id_box),
        "id_bar_history": dict(engine.id_box._bar_history),
        "id_bars_since_refit": dict(engine.id_box._bars_since_refit),
        "hysteresis_map_object": id(engine._hmm_risk_hysteresis),
        "hysteresis_items": {
            symbol: id(controller)
            for symbol, controller in engine._hmm_risk_hysteresis.items()
        },
        "outcome_object": id(engine.closed_loop.outcomes),
        "outcomes": list(engine.closed_loop.outcomes._outcomes),
        "open_trades": dict(engine.open_trades),
        "prepared": bool(getattr(engine, "_morning_recovery_prepared", False)),
    }


def _tamper_boot(store, mutate):
    journal = StateRecoveryJournal(store.connection)
    state = journal.load_boot()
    mutate(state)
    journal.checkpoint_boot(state)


@pytest.mark.parametrize(
    "field",
    ["id_v1", "hmm_hysteresis_v1", "outcomes_v1"],
)
def test_c04a_missing_payload_fails_closed_before_broker_side_effect(
    tmp_path, field
):
    first, _, store, truth = carry(tmp_path)
    store.close()

    second, _, store2 = boot(tmp_path)
    _tamper_boot(store2, lambda state: state.pop(field))

    broker = recovered_broker(truth)
    before = _fresh_controller_snapshot(second)

    with pytest.raises((RuntimeError, ValueError)):
        second._reconcile_morning_startup(
            account_id="fixture-account",
            broker=broker,
        )

    assert broker._simulated_gtts == {}
    assert _fresh_controller_snapshot(second) == before
    assert second._execution_halted
    assert not getattr(second, "_morning_recovery_prepared", False)
    store2.close()


def test_c04a_corrupt_id_payload_fails_atomically(tmp_path):
    first, _, store, truth = carry(tmp_path)
    store.close()

    second, _, store2 = boot(tmp_path)

    def corrupt(state):
        state["id_v1"]["config_hash"] = "wrong"

    _tamper_boot(store2, corrupt)

    broker = recovered_broker(truth)
    before = _fresh_controller_snapshot(second)

    with pytest.raises(ValueError):
        second._reconcile_morning_startup(
            account_id="fixture-account",
            broker=broker,
        )

    assert broker._simulated_gtts == {}
    assert _fresh_controller_snapshot(second) == before
    assert second._execution_halted
    assert not getattr(second, "_morning_recovery_prepared", False)
    store2.close()


def test_c04a_unknown_hysteresis_symbol_fails_atomically(tmp_path):
    first, _, store, truth = carry(tmp_path)
    store.close()

    second, _, store2 = boot(tmp_path)

    def corrupt(state):
        state["hmm_hysteresis_v1"]["NOT_IN_UNIVERSE"] = {}

    _tamper_boot(store2, corrupt)

    broker = recovered_broker(truth)
    before = _fresh_controller_snapshot(second)

    with pytest.raises(
        ValueError, match="outside the engine universe"
    ):
        second._reconcile_morning_startup(
            account_id="fixture-account",
            broker=broker,
        )

    assert broker._simulated_gtts == {}
    assert _fresh_controller_snapshot(second) == before
    assert second._execution_halted
    assert not getattr(second, "_morning_recovery_prepared", False)
    store2.close()


def test_c04a_corrupt_hysteresis_payload_fails_atomically(tmp_path):
    first, _, store, truth = carry(tmp_path)

    hysteresis = HMMRiskHysteresis()
    hysteresis.configure(first.config)
    for _ in range(20):
        hysteresis.update(
            {"available": True, "stress_probability": 0.99}
        )
    first._hmm_risk_hysteresis["TITAN"] = hysteresis
    first._checkpoint_morning_recovery(
        account_id="fixture-account",
        timestamp=pd.Timestamp("2023-12-05 15:26"),
    )
    store.close()

    second, _, store2 = boot(tmp_path)

    def corrupt(state):
        state["hmm_hysteresis_v1"]["TITAN"]["state"]["enter_count"] = True

    _tamper_boot(store2, corrupt)

    broker = recovered_broker(truth)
    before = _fresh_controller_snapshot(second)

    with pytest.raises(ValueError):
        second._reconcile_morning_startup(
            account_id="fixture-account",
            broker=broker,
        )

    assert broker._simulated_gtts == {}
    assert _fresh_controller_snapshot(second) == before
    assert second._execution_halted
    assert not getattr(second, "_morning_recovery_prepared", False)
    store2.close()


def test_c04a_corrupt_outcome_payload_fails_atomically(tmp_path):
    first, _, store, truth = carry(tmp_path)

    first.closed_loop.outcomes.record(
        {
            "symbol": "TITAN",
            "side": "BUY",
            "regime": "calm",
            "net_pnl": 125.0,
            "trade_id": "c04a-corrupt-outcome",
            "metadata": {},
        }
    )
    first._checkpoint_morning_recovery(
        account_id="fixture-account",
        timestamp=pd.Timestamp("2023-12-05 15:26"),
    )
    store.close()

    second, _, store2 = boot(tmp_path)

    def corrupt(state):
        state["outcomes_v1"]["outcomes"][0]["net_pnl"] = "not-a-number"

    _tamper_boot(store2, corrupt)

    broker = recovered_broker(truth)
    before = _fresh_controller_snapshot(second)

    with pytest.raises(ValueError):
        second._reconcile_morning_startup(
            account_id="fixture-account",
            broker=broker,
        )

    assert broker._simulated_gtts == {}
    assert _fresh_controller_snapshot(second) == before
    assert second._execution_halted
    assert not getattr(second, "_morning_recovery_prepared", False)
    store2.close()
