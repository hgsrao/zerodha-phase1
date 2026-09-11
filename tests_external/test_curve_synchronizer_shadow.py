import numpy as np
import pandas as pd

from revision2_external.curve_synchronizer_shadow import CurveSynchronizerShadow
from revision2_external.study_entry_shadow import StudyEntryShadowLedger
from revision2_external.intraday_pnl_setpoint_shadow import IntradayNetPnlBandShadow


def _bars() -> pd.DataFrame:
    # Enough causal cyclic history for TA-Lib's Hilbert warm-up, followed by a
    # positive slope reversal with elevated volume.
    x = np.linspace(0, 8 * np.pi, 90)
    close = 100.0 + np.sin(x)
    close[-2] = 99.5
    close[-1] = 100.4
    return pd.DataFrame({
        "open": close - 0.05, "high": close + 0.2, "low": close - 0.2,
        "close": close, "volume": [100.0] * 89 + [200.0],
    })


def test_curve_synchronizer_emits_causal_curve_telemetry_and_never_fills_same_bar():
    bars = _bars()
    ledger = StudyEntryShadowLedger()
    sync = CurveSynchronizerShadow(ledger)
    observation = sync.observe("TEST", 89, "t89", bars.iloc[-1], bars, atr=0.5)
    assert observation["curve_ready"] is True
    assert observation["phase_angle_degrees"] is not None
    assert observation["phase_velocity_degrees_per_bar"] is not None
    assert observation["cycle_amplitude_atr"] >= 0.0
    # A decision bar only queues a hypothesis; it has no same-bar execution.
    assert not ledger.resolved
    assert len(ledger._pending) in (0, 1)


def test_generic_schedule_keeps_next_bar_fill_and_terminal_bar_order_unknown():
    ledger = StudyEntryShadowLedger(max_hold_bars=4)
    ledger.schedule(symbol="TEST", index=10, side="BUY", setup_extreme=99.0, atr=1.0, observation={"timestamp": "t10"})
    ledger.advance("TEST", 11, "t11", pd.Series({"open": 101.0, "high": 110.0, "low": 90.0, "close": 101.0}))
    assert not ledger.resolved
    ledger.advance("TEST", 12, "t12", pd.Series({"open": 101.0, "high": 105.0, "low": 100.0, "close": 104.0}))
    assert ledger.resolved[0]["entry_timestamp"] == "t11"


def test_curve_observation_has_no_future_bar_fields():
    bars = _bars()
    ledger = StudyEntryShadowLedger()
    CurveSynchronizerShadow(ledger).observe("TEST", 89, "t89", bars.iloc[-1], bars, atr=0.5)
    observation = ledger.observations[-1]
    assert "next_open" not in observation
    assert "future" not in " ".join(observation)


def test_daily_net_pnl_setpoint_includes_cost_net_and_never_chases_losses():
    controller = IntradayNetPnlBandShadow(50.0, 50.0, quantity=10)
    controller.record_outcome({"candidate_id": "x", "resolution_timestamp": "t", "net_pnl_per_share": -3.0})
    assert controller.net_pnl_rupees == -30.0
    assert controller.allow_entry() is True
    assert controller.throttle == 0.4
    controller.record_outcome({"candidate_id": "y", "resolution_timestamp": "t2", "net_pnl_per_share": -3.0})
    assert controller.state == "LOSS_LIMIT_HALTED"
    assert controller.allow_entry() is False
