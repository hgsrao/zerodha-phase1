from datetime import datetime

import pytest

from revision5.ccpp_unified_plant import (
    LOSS_COOLDOWN_BARS,
    CentralPlantMasterDCS,
)
from revision5.engine_state import (
    ENGINE_A,
    ENGINE_B,
)
from revision5.topology import (
    BPSTG_HEALTHCARE,
    GTG1_HEAVY_INDUSTRY,
    GTG2_TECH_TELECOM,
)


@pytest.fixture
def plant(tmp_path):
    return CentralPlantMasterDCS(
        total_capital=1_000_000.0,
        db_path=(
            tmp_path
            / "r5_state.sqlite3"
        ),
    )


def safe_entry(
    plant,
    *,
    engine,
    symbol,
    dt,
    bar_index,
):
    return plant.evaluate_entry(
        engine_mode=engine,
        symbol=symbol,
        bar_dt=dt,
        bar_index=bar_index,
        z_score=-3.0,
        price=1500.0,
        atr=15.0,
        bid=1499.8,
        ask=1500.2,
        tick_age_s=1.0,
        bar_range=16.0,
        nifty_15m_ret=0.0,
        nifty_vol_z=0.0,
        fleet_equity_dd_pct=0.0,
    )


def test_exactly_five_bays_and_48_symbols(plant):
    assert len(plant.bays) == 5

    total = sum(
        len(bay.symbols)
        for bay in plant.bays.values()
    )

    assert total == 48


def test_engine_a_normal_admission(plant):
    result = safe_entry(
        plant,
        engine=ENGINE_A,
        symbol="INFY",
        dt=datetime(
            2026, 7, 31, 10, 30
        ),
        bar_index=75,
    )

    assert result["admitted"] is True
    assert (
        result["bay_id"]
        == GTG2_TECH_TELECOM
    )
    assert result["quantity"] > 0


def test_engine_a_cutoff_is_enforced(plant):
    result = safe_entry(
        plant,
        engine=ENGINE_A,
        symbol="INFY",
        dt=datetime(
            2026, 7, 31, 14, 30
        ),
        bar_index=315,
    )

    assert result == {
        "admitted": False,
        "reason": (
            "ENGINE_A_ENTRY_"
            "CUTOFF_14_30_IST"
        ),
    }


def test_engine_b_bypasses_engine_a_1430_rule(plant):
    result = safe_entry(
        plant,
        engine=ENGINE_B,
        symbol="INFY",
        dt=datetime(
            2026, 7, 31, 14, 45
        ),
        bar_index=330,
    )

    assert result["admitted"] is True


def test_engine_a_latch_is_consumed_only_on_fill(plant):
    dt = datetime(
        2026, 7, 31, 10, 30
    )

    first = safe_entry(
        plant,
        engine=ENGINE_A,
        symbol="INFY",
        dt=dt,
        bar_index=75,
    )

    assert first["admitted"]

    # Admission alone must not burn allowance.
    second = safe_entry(
        plant,
        engine=ENGINE_A,
        symbol="TCS",
        dt=dt,
        bar_index=75,
    )

    assert second["admitted"]

    assert plant.on_trade_filled(
        engine_mode=ENGINE_A,
        symbol="INFY",
        filled_at=dt,
    )

    rejected = safe_entry(
        plant,
        engine=ENGINE_A,
        symbol="TCS",
        dt=datetime(
            2026, 7, 31, 10, 31
        ),
        bar_index=76,
    )

    assert rejected["admitted"] is False
    assert (
        rejected["reason"]
        == "ENGINE_A_DAILY_BAY_FILL_ALREADY_USED"
    )


def test_engine_b_fill_does_not_burn_a_latch(plant):
    dt = datetime(
        2026, 7, 31, 11, 0
    )

    assert plant.on_trade_filled(
        engine_mode=ENGINE_B,
        symbol="INFY",
        filled_at=dt,
    )

    result = safe_entry(
        plant,
        engine=ENGINE_A,
        symbol="TCS",
        dt=dt,
        bar_index=105,
    )

    assert result["admitted"] is True


def test_loss_cooldown_is_true_15_bar_lock(plant):
    bay = plant.bays[
        GTG1_HEAVY_INDUSTRY
    ]

    plant.on_trade_closed(
        symbol="RELIANCE",
        pnl_r=-1.0,
        reason="STOP_LOSS",
        closed_at=datetime(
            2026, 7, 31, 11, 0
        ),
        bar_index=100,
    )

    assert (
        bay.cooldown_remaining(101)
        == LOSS_COOLDOWN_BARS
    )

    # Scanning many symbols cannot decrement it.
    assert bay.cooldown_remaining(101) == 15
    assert bay.cooldown_remaining(101) == 15

    assert bay.cooldown_remaining(115) == 1
    assert bay.cooldown_remaining(116) == 0


def test_two_losses_trip_bay_then_new_day_resets(plant):
    bay = plant.bays[
        BPSTG_HEALTHCARE
    ]

    plant.on_trade_closed(
        symbol="SUNPHARMA",
        pnl_r=-1.0,
        reason="STOP_LOSS",
        closed_at=datetime(
            2026, 7, 31, 11, 0
        ),
        bar_index=100,
    )

    plant.on_trade_closed(
        symbol="SUNPHARMA",
        pnl_r=-1.0,
        reason="STOP_LOSS",
        closed_at=datetime(
            2026, 7, 31, 12, 0
        ),
        bar_index=160,
    )

    assert bay.consecutive_stops == 2
    assert bay.tripped_offline is True

    plant.begin_bar(
        datetime(
            2026, 8, 1, 9, 15
        ),
        0,
    )

    assert bay.consecutive_stops == 0
    assert bay.tripped_offline is False
    assert bay.cooldown_remaining(0) == 0


def test_dispatcher_feedback_preserves_total_weight(plant):
    initial = plant.dispatcher.weights[
        GTG1_HEAVY_INDUSTRY
    ]

    for offset, value in enumerate(
        (1.8, 2.1, 1.5)
    ):
        plant.on_trade_closed(
            symbol="RELIANCE",
            pnl_r=value,
            reason="TARGET_HIT",
            closed_at=datetime(
                2026,
                7,
                31,
                11,
                offset,
            ),
            bar_index=100 + offset,
        )

    new_weight = plant.dispatcher.weights[
        GTG1_HEAVY_INDUSTRY
    ]

    assert new_weight > initial

    assert sum(
        plant.dispatcher.weights.values()
    ) == pytest.approx(1.0)


def test_squareoff_intents_only_include_engine_a(plant):
    positions = [
        {
            "position_id": "A1",
            "engine_mode": ENGINE_A,
            "symbol": "INFY",
        },
        {
            "position_id": "B1",
            "engine_mode": ENGINE_B,
            "symbol": "SUNPHARMA",
        },
        {
            "position_id": "A2",
            "engine_mode": ENGINE_A,
            "symbol": "HDFCBANK",
        },
    ]

    before = (
        plant.build_engine_a_squareoff_intents(
            current_dt=datetime(
                2026, 7, 31, 15, 14, 59
            ),
            open_positions=positions,
        )
    )

    assert before == []

    due = (
        plant.build_engine_a_squareoff_intents(
            current_dt=datetime(
                2026, 7, 31, 15, 15
            ),
            open_positions=positions,
        )
    )

    assert len(due) == 2
    assert {
        intent.symbol
        for intent in due
    } == {
        "INFY",
        "HDFCBANK",
    }

    assert all(
        intent.engine_mode == ENGINE_A
        for intent in due
    )


def test_unmapped_symbol_fails_closed(plant):
    result = safe_entry(
        plant,
        engine=ENGINE_A,
        symbol="NOT_A_SYMBOL",
        dt=datetime(
            2026, 7, 31, 10, 0
        ),
        bar_index=45,
    )

    assert result == {
        "admitted": False,
        "reason": (
            "UNMAPPED_SYMBOL:NOT_A_SYMBOL"
        ),
    }


def test_bar_index_cannot_go_backwards(plant):
    plant.begin_bar(
        datetime(
            2026, 7, 31, 10, 0
        ),
        100,
    )

    with pytest.raises(ValueError):
        plant.begin_bar(
            datetime(
                2026, 7, 31, 10, 1
            ),
            99,
        )
