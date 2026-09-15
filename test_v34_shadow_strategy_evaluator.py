from pathlib import Path

import pytest

from shadow_strategy_evaluator import evaluate_shadow_strategy, write_telemetry_atomic


def quotes():
    return {
        "NSE:AAA": {"last_price": 100, "net_change": 3, "ohlc": {"high": 105, "low": 98}},
        "NSE:BBB": {"last_price": 200, "net_change": -1, "ohlc": {"high": 220, "low": 180}},
    }


def depth(obi_positive=True):
    return {"AAA": {"buy": [{"quantity": 80 if obi_positive else 40}],
                    "sell": [{"quantity": 20 if obi_positive else 60}]}}


def test_hypothetical_buy_has_no_execution_capability():
    result = evaluate_shadow_strategy(quotes=quotes(), universe=["AAA", "BBB"],
                                      depth_by_symbol=depth(), risk_allowed=True)
    assert result["decision"] == "HYPOTHETICAL_BUY"
    assert result["candidate"]["symbol"] == "AAA"
    assert not any(result["capabilities"].values())


def test_one_symbol_fails_closed():
    result = evaluate_shadow_strategy(quotes={"NSE:AAA": quotes()["NSE:AAA"]},
                                      universe=["AAA"], depth_by_symbol=depth())
    assert result["decision"] == "BLOCK"


def test_low_obi_blocks_candidate():
    result = evaluate_shadow_strategy(quotes=quotes(), universe=["AAA", "BBB"],
                                      depth_by_symbol=depth(False), risk_allowed=True)
    assert result["decision"] == "BLOCK"


def test_malformed_depth_blocks_candidate():
    result = evaluate_shadow_strategy(quotes=quotes(), universe=["AAA", "BBB"],
                                      depth_by_symbol={"AAA": {"buy": "bad", "sell": []}},
                                      risk_allowed=True)
    assert result["decision"] == "BLOCK"


def test_unaffordable_candidate_blocks():
    result = evaluate_shadow_strategy(quotes=quotes(), universe=["AAA", "BBB"],
                                      depth_by_symbol=depth(), trial_capital=1,
                                      risk_allowed=True)
    assert result["decision"] == "BLOCK"


def test_risk_must_be_explicitly_supplied():
    result = evaluate_shadow_strategy(quotes=quotes(), universe=["AAA", "BBB"],
                                      depth_by_symbol=depth())
    assert result["decision"] == "BLOCK"
    assert result["checkpoints"][-1]["name"] == "risk_authorization"


def test_writer_refuses_production_state(tmp_path: Path):
    with pytest.raises(ValueError):
        write_telemetry_atomic(tmp_path / "bot_state_v34.json", {"safe": True})


def test_writer_creates_separate_telemetry(tmp_path: Path):
    target = tmp_path / "shadow_strategy_telemetry.json"
    write_telemetry_atomic(target, {"mode": "READ_ONLY_SHADOW"})
    assert 'READ_ONLY_SHADOW' in target.read_text(encoding="utf-8")
