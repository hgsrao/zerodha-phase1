from portfolio_brain_v9 import PortfolioConfig, SECTORS, position_quantity


def test_sector_map_has_fifty_one_symbols():
    # Was 20 (frozen) - deliberately extended to 51 on 2026-08-16 (the
    # original 20 plus 31 more Nifty-48 symbols) - see
    # p01d-and-v11-bridge-status memory for the full record.
    assert len(SECTORS) == 51


def test_position_quantity_obeys_risk_and_notional_caps():
    cfg = PortfolioConfig()
    quantity = position_quantity(100_000, 100_000, 0, 500, 10, cfg)
    assert quantity == 25  # ₹250 risk allowance / ₹10 risk per share


def test_position_quantity_obeys_deployment_room():
    cfg = PortfolioConfig()
    quantity = position_quantity(100_000, 100_000, 79_000, 500, 1, cfg)
    assert quantity == 2
