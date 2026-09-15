from datetime import datetime,timezone
from pathlib import Path
import pytest
import live_input_shadow_lifecycle as s

def sample(price="100",obi=True):
    names=["quote_integrity","universe_coverage","momentum","stability","affordability","obi"]
    return {"observed_at_utc":datetime.now(timezone.utc).isoformat(),"candidate":{"symbol":"ABC","last_price":price,"tranche_qty":10,"score":1.2,"obi":.5},"checkpoints":[{"name":n,"passed":obi if n=="obi" else True} for n in names]}
def test_waits_for_all_market_gates():
    state=s.initial_state(); assert s.step(state,sample(obi=False))=="NO_ACTION"; assert state["active_trade"] is None
def test_creates_shadow_entry_and_protection():
    state=s.initial_state(); assert s.step(state,sample())=="SHADOW_ENTRY"; trade=state["active_trade"]
    assert trade["entry_order_id"].startswith("SHADOW-") and trade["stop_price"]=="98.00"
    assert state["safety"]["broker_write"] is False
def test_live_prices_manage_and_exit():
    state=s.initial_state(); s.step(state,sample()); assert s.step(state,sample("102"))=="MANAGED"; assert s.step(state,sample("104"))=="SHADOW_EXIT"; assert state["completed_trades"][0]["realised_pnl"]=="40.00"
def test_symbol_change_is_fail_safe_no_action():
    state=s.initial_state(); s.step(state,sample()); changed=sample("101"); changed["candidate"]["symbol"]="XYZ"; assert s.step(state,changed)=="NO_ACTION"; assert state["active_trade"]["last_price"]=="100"
def test_writer_refuses_protected_targets(tmp_path):
    for name in ("bot_state_v34.json","bot_state_v34.lock","shadow_strategy_telemetry.json"):
        with pytest.raises(ValueError): s.write_state(tmp_path/name,{})
def test_source_has_no_broker_or_production_imports():
    source=Path(s.__file__).read_text(encoding="utf-8").lower(); forbidden=("kiteconnect",".place_order(",".modify_order(",".cancel_order(","request_entry(","run_production_p01d_candidate","institutional_engine_v34_p01d_candidate")
    assert all(x not in source for x in forbidden)
