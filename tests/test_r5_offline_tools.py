"""Offline R5 tools: loss attribution and the parameter inventory."""
from scripts.r5_loss_attribution import attribute, to_markdown
from scripts.r5_parameter_inventory import classify_parameters, engine_files, scan_literals
from canonical_parameter_registry import CanonicalParameterRegistry


def _trade(symbol, side, pnl, costs, reason, mfe_r, hour="10"):
    return {"symbol": symbol, "side": side, "pnl": pnl, "costs": costs, "net_pnl": pnl - costs,
            "reason": reason, "mfe_r": mfe_r, "bars_held": 4,
            "entry_timestamp": f"2024-03-01 {hour}:15:00+05:30"}


def test_loss_attribution_separates_edge_from_costs_and_entry_from_exit():
    trades = [_trade("INFY", "BUY", 100.0, 40.0, "target", 2.0),
              _trade("TCS", "SELL", -50.0, 40.0, "stop", 0.1),
              _trade("INFY", "BUY", -20.0, 40.0, "governor_exit:FSR_BELOW_EXIT:FSRN", 1.4, hour="14")]
    result = attribute(trades)
    assert result["total"]["gross_pnl"] == 30.0 and result["total"]["costs"] == 120.0
    assert result["total"]["net_pnl"] == -90.0
    assert set(result["by_exit_reason"]) == {"target", "stop", "governor_exit"}
    assert result["excursion"]["losers_that_reached_1R"] == 1
    assert result["excursion"]["losers_never_above_0_25R"] == 1
    assert "Exit reason" in to_markdown(result, ["fixture.json"])


def test_parameter_inventory_covers_every_registry_parameter_once():
    registry = CanonicalParameterRegistry()
    rows = classify_parameters(registry)
    names = [name for _, name, _ in rows]
    assert len(names) == len(registry.params) + len(registry.safety_params)
    assert {cls for cls, _, _ in rows} <= {"STRUCTURAL", "FIXED_SAFETY", "CALIBRATABLE_OFFLINE",
                                            "DYNAMIC_SCHEDULED", "FIXED_ENGINEERING"}
    governor = dict((name, cls) for cls, name, _ in rows)
    assert governor["mv_fsr_entry_threshold"] == "CALIBRATABLE_OFFLINE"


def test_literal_scan_reports_file_line_and_kind():
    rows = scan_literals(engine_files())
    assert rows and all({"file", "line", "value", "kind", "scope"} <= set(r) for r in rows)
    assert any(r["file"] == "revision5/governor_authority.py" for r in rows)
