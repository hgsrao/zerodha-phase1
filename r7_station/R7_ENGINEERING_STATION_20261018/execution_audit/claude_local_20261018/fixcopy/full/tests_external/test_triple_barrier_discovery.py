import pandas as pd
from revision2_external.triple_barrier_discovery import _label, extract_day

def test_label_refuses_ambiguous_terminal_bar():
    bars=pd.DataFrame([{"high":102,"low":98}])
    assert _label(100,1,"BUY",bars)=="INTRABAR_ORDER_UNKNOWN"

def test_extracts_two_directional_rows_from_completed_bars():
    n=100
    x=list(range(n))
    d=pd.DataFrame({"timestamp":pd.date_range("2025-01-02 09:15",periods=n,freq="min"),"open":[100+.02*i for i in x],"high":[100.2+.02*i for i in x],"low":[99.8+.02*i for i in x],"close":[100+.02*i for i in x],"volume":[1000+i for i in x]})
    rows=extract_day("X",d)
    assert rows and {r["side"] for r in rows}=={"BUY","SELL"}
    assert all(r["barrier_price"]>=r["atr14"] for r in rows)
