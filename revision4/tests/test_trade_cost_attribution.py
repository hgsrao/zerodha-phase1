from revision4.trade_cost_attribution import attribution_report


def test_attribution_reconciles_and_groups_authoritative_completed_trades():
    ledger = [
        {
            "trade_id": "1", "symbol": "INFY", "entry_timestamp": "2023-10-02T04:00:00+00:00",
            "exit_timestamp": "2023-10-02T04:05:00+00:00", "entry_price": 100.0,
            "exit_price": 110.0, "quantity": 10, "entry_cost": 3.0, "exit_cost": 4.0,
            "gross_pnl": 100.0, "net_pnl": 93.0, "exit_reason": "target", "same_session": True,
        },
        {
            "trade_id": "2", "symbol": "TCS", "entry_timestamp": "2023-10-02T04:10:00+00:00",
            "exit_timestamp": "2023-10-02T04:12:00+00:00", "entry_price": 200.0,
            "exit_price": 195.0, "quantity": 10, "entry_cost": 2.0, "exit_cost": 3.0,
            "gross_pnl": -50.0, "net_pnl": -55.0, "exit_reason": "stop", "same_session": True,
        },
    ]
    report = attribution_report(ledger)
    assert report["gross_pnl"] == 50.0
    assert report["total_cost"] == 12.0
    assert report["net_pnl"] == 38.0
    assert report["trades"][0]["entry_hour_ist"] == 9
    assert {item["symbol"] for item in report["by_symbol"]} == {"INFY", "TCS"}
