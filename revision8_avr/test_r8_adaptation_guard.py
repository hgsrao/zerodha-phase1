"""Default R8 runs without adaptation; the host engine's own profile consumption is always refused (R8 owns it)."""
import json
from pathlib import Path
import pandas as pd
import pytest

from revision5.ccpp_unified_plant import CentralPlantMasterDCS
from revision2_external.grid_context import SealedGridContextProvider
from tests.test_r5_parameter_authority import registry_with
from . import Revision8Engine
from .r8_state import ControlConfig
from .test_r8_demand_allocation import engine, run_bars

HERE = Path(__file__).resolve().parent


def plant_kwargs():
    times = pd.date_range("2024-03-01 09:15", periods=100, freq="15min", tz="Asia/Kolkata")
    provider = SealedGridContextProvider(pd.DataFrame({"timestamp": times, "close": 21000.0}),
                                         pd.DataFrame({"timestamp": times, "close": 15.0}))
    return dict(registry=registry_with(), real_plant_dcs=CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:"),
                plant_control_mode="PAPER_APPLY", grid_context_provider=provider)


CFG = ControlConfig('INFY', 'GTG2_TECH_TELECOM', 0.02)


def test_engine_constructs_with_a_paper_apply_plant_when_adaptation_is_off():
    e = Revision8Engine(['INFY'], r8_configs=[CFG], **plant_kwargs())
    assert e.dynamic_profile_mode == 'off' and e.r8_adaptation_enabled is False and e.r8_adaptation_mode == 'off'


def test_engine_refuses_dynamic_profile_consumption():
    with pytest.raises(ValueError, match='adaptation'):
        Revision8Engine(['INFY'], r8_configs=[CFG], dynamic_profile_mode='consume', **plant_kwargs())


def test_engine_has_no_adaptation_switch():
    with pytest.raises(TypeError):
        Revision8Engine(['INFY'], r8_configs=[CFG], full_adaptation=True)


def test_checkpoint_declares_adaptation_off_and_rejects_anything_else():
    e = engine(['INFY']); run_bars(e, 10)
    snap = json.loads(json.dumps(e.r8_snapshot()))
    assert snap['adaptation'] == 'off'
    snap['adaptation'] = 'on'
    with pytest.raises(ValueError):
        e.r8_restore(snap)


def test_replay_harness_exposes_only_r8_adaptation_modes():
    text = (HERE / 'run_r8_replay.py').read_text()
    assert 'dynamic_profile_mode' not in text and 'full_adaptation' not in text
    assert 'choices=["off", "shadow", "paper"]' in text


def test_r8_parameters_are_immutable_during_a_run():
    e = engine(['INFY']); before = {s: c for s, c in e.r8_configs.items()}
    run_bars(e, 80)
    assert e.r8_configs == before
    with pytest.raises(Exception):
        e.r8_configs['INFY'].kp = 9.0                                         # frozen dataclass
