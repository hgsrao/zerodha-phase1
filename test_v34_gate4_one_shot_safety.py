import json
from decimal import Decimal
from pathlib import Path

import gate4_one_shot_safety as safety


BASE = Path(__file__).resolve().parent

# Isolation fix (2026-08-14): these tests used to point shadow_path/
# production_state_path directly at the live, currently-running
# shadow_strategy_telemetry.json / bot_state_v34.json in the project root.
# That worked only by coincidence while those files held stale fixture-like
# data from earlier testing; once the real read-only collectors started
# running today and correctly updating those files with live market data,
# any test asserting a specific hardcoded symbol/price broke - not because
# anything was wrong, but because the test was never isolated from live
# state to begin with. Fixed by writing controlled, static fixtures to
# tmp_path instead. production_runner_path is deliberately NOT isolated -
# checking the real run_production_p01d_candidate.py's actual safety-flag
# source text is the whole point of that part of the check, and that file's
# content is stable/reviewed, not live market data.


def _write_fixture_shadow(path: Path, *, symbol="HDFCBANK", last_price=725.0, target_qty=2):
    path.write_text(json.dumps({
        "source": "KITE_QUOTE_READ_ONLY",
        "decision": "BLOCK",
        "candidate": {
            "symbol": symbol, "last_price": last_price,
            "score": 0.5, "tranche_qty": target_qty // 2, "target_qty": target_qty,
            "obi": 0.3,
        },
    }), encoding="utf-8")


def _write_fixture_state(path: Path):
    path.write_text(json.dumps({
        "status": "FLAT", "active_trade": None, "clearance_required": False,
        "realised_net_pnl": "0", "unrealised_mtm": "0",
    }), encoding="utf-8")


def test_cached_observation_and_simulated_entry_exit_are_blocked_offline(tmp_path):
    shadow_path = tmp_path / "shadow_strategy_telemetry.json"
    state_path = tmp_path / "bot_state_v34.json"
    _write_fixture_shadow(shadow_path)
    _write_fixture_state(state_path)

    result = safety.run_one_shot(
        shadow_path=shadow_path,
        production_state_path=state_path,
        production_runner_path=BASE / "run_production_p01d_candidate.py",
    )

    assert result["cached_observation"] == {
        "source": "KITE_QUOTE_READ_ONLY", "decision": "BLOCK",
        "entry_generated": False,
    }
    assert [row["transaction_type"] for row in result["captured_intents"]] == [
        "BUY", "SELL",
    ]
    assert result["captured_intents"][0]["tag"] == "V3.4_ENTRY"
    assert result["captured_intents"][1]["tag"] == "V3.4_EXIT"
    assert result["captured_intents"][1]["order_type"] == "SL-M"
    assert len(result["gate4_blocks"]) == 2
    assert all("GATE_4_LOCKED" in reason for reason in result["gate4_blocks"])
    assert result["external_broker_calls"] == 0
    assert result["live_trading_enabled"] is False
    assert result["runner_started"] is False
    assert result["production_state_unchanged"] is True


def test_exit_uses_only_an_in_memory_simulated_position(tmp_path):
    shadow_path = tmp_path / "shadow_strategy_telemetry.json"
    state_path = tmp_path / "bot_state_v34.json"
    _write_fixture_shadow(shadow_path, symbol="HDFCBANK", last_price=725.0)
    _write_fixture_state(state_path)

    result = safety.run_one_shot(
        shadow_path=shadow_path,
        production_state_path=state_path,
        production_runner_path=BASE / "run_production_p01d_candidate.py",
        tick_size=Decimal("0.05"),
    )
    assert result["simulated_position"]["source"] == "IN_MEMORY_ONLY"
    assert result["simulated_position"]["symbol"] == "HDFCBANK"
    assert result["captured_intents"][1]["trigger_price"] == "724.95"


def test_harness_contains_no_broker_or_network_dependencies():
    source = Path(safety.__file__).read_text(encoding="utf-8").lower()
    forbidden = (
        "kiteconnect", "requests", "access_token", "request_token",
        ".place_order(", ".modify_order(", ".cancel_order(",
        "run_production_p01d_candidate import",
    )
    assert all(token not in source for token in forbidden)
