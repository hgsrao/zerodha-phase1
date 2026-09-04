import pytest

from revision2.transaction_costs import equity_intraday_leg
from revision2.orchestrator import Revision2Orchestrator
from tests.test_revision2_causal_sensitivity import _synthetic_bars


def test_buy_leg_includes_brokerage_exchange_sebi_gst_and_stamp():
    leg = equity_intraday_leg(100.0, 1000, "BUY")
    assert leg.turnover == 100_000
    assert leg.brokerage == 20.0
    assert leg.stt == 0.0
    assert leg.exchange_transaction_charge == pytest.approx(3.07)
    assert leg.sebi_charge == pytest.approx(0.1)
    assert leg.stamp_duty == pytest.approx(3.0)
    assert leg.gst == pytest.approx(0.18 * (20.0 + 3.07 + 0.1))


def test_sell_leg_includes_intraday_stt_and_no_stamp():
    leg = equity_intraday_leg(100.0, 1000, "SELL")
    assert leg.stt == pytest.approx(25.0)
    assert leg.stamp_duty == 0.0


@pytest.mark.parametrize("price,quantity,side", [(0, 1, "BUY"), (1, 0, "BUY"), (1, 1, "HOLD")])
def test_invalid_leg_fails_closed(price, quantity, side):
    with pytest.raises(ValueError):
        equity_intraday_leg(price, quantity, side)


def test_reported_net_pnl_reconciles_to_trade_ledger():
    report = Revision2Orchestrator("TEST").run(_synthetic_bars(), warmup=40)
    assert report["completed_trades"] > 0
    assert report["ledger_net_pnl"] == pytest.approx(report["net_pnl"], abs=1e-3)
    assert sum(t["costs"] for t in report["trades"]) == pytest.approx(report["total_cost"], abs=1e-3)
