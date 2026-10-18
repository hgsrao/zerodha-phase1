import pytest

from scripts.attribute_external_macro_shadow import build_attribution


def _report(with_labels=True):
    observation = {
        "symbol": "SUNPHARMA", "decision_timestamp": "2023-09-01T09:30:00+05:30",
        "available": True, "synchronized": False, "phase_delta_degrees": 90.0,
    }
    if with_labels:
        observation.update({"nifty_ema_50": 19_900.0, "macro_nifty_trend": 1,
                            "macro_vix_level": 14.5, "macro_vix_slope": 0.01})
    return {
        "identity": {key: "sealed" for key in (
            "stock_manifest_hash", "context_manifest_hash", "config_hash", "safety_contract_hash",
        )},
        "grid_shadow": {"observations": [observation]},
        "controller_telemetry": [{"event_type": "ENTRY_CONFIDENCE_THROTTLE", "candidate_id": "c1",
                                    "symbol": "SUNPHARMA", "timestamp": "2023-09-01T09:30:00+05:30"}],
        "trades": [{"trade_id": "t1", "candidate_id": "c1", "symbol": "SUNPHARMA",
                    "net_pnl": -12.0, "reason": "stop"}],
    }


def test_attribution_joins_trade_to_causal_macro_labels():
    result = build_attribution([_report()])
    assert result["coverage"]["all_completed_trades_joined"] is True
    assert result["aggregate"]["net_pnl"] == -12.0
    assert result["by_nifty_trend"]["nifty_up"]["trades"] == 1
    assert result["by_vix_slope"]["vix_rising"]["trades"] == 1


def test_attribution_fails_closed_when_macro_labels_are_missing():
    with pytest.raises(ValueError, match="missing macro labels"):
        build_attribution([_report(with_labels=False)])
