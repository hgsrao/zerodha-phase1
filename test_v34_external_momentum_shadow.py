from external_momentum_shadow import EXTERNAL_UNIVERSE, evaluate_external_momentum, formation_prices


def test_external_variant_is_paper_only_and_selects_four():
    _, _, pairs = formation_prices()
    quotes = {f"NSE:{symbol}": {"last_price": recent * 1.01}
              for symbol, (_, recent) in pairs.items()}
    result = evaluate_external_momentum(quotes)
    assert result["variant_id"] == "EXTERNAL_CROSS_SECTIONAL_12_1"
    assert result["status"] == "MARKED"
    assert len(result["selected"]) == 4
    assert result["broker_write"] is False
    assert result["order_api"] is False
    assert result["authoritative_strategy"] is False


def test_external_variant_fails_visible_when_quote_missing():
    result = evaluate_external_momentum({})
    assert result["status"] == "WAITING_FOR_ALL_QUOTES"
    assert result["paper_equity"] is None


def test_universe_excludes_polycab_and_matches_the_expanded_fifty():
    # Was 19 (frozen, 20-symbol SECTORS minus POLYCAB) - deliberately
    # extended to 50 on 2026-08-16 (51-symbol SECTORS minus POLYCAB) -
    # see p01d-and-v11-bridge-status memory for the full record.
    assert "POLYCAB" not in EXTERNAL_UNIVERSE
    assert len(EXTERNAL_UNIVERSE) == 50


def test_default_paper_capital_matches_the_validated_real_trial_size():
    _, _, pairs = formation_prices()
    quotes = {f"NSE:{symbol}": {"last_price": recent * 1.01}
              for symbol, (_, recent) in pairs.items()}
    result = evaluate_external_momentum(quotes)
    assert result["paper_starting_capital"] == 100_000.0


def test_real_costs_reduce_paper_equity_versus_a_cost_free_mark():
    _, _, pairs = formation_prices()
    quotes = {f"NSE:{symbol}": {"last_price": recent * 1.01}
              for symbol, (_, recent) in pairs.items()}
    with_costs = evaluate_external_momentum(quotes, apply_real_costs=True)
    without_costs = evaluate_external_momentum(quotes, apply_real_costs=False)
    assert with_costs["real_costs_applied"] is True
    assert without_costs["real_costs_applied"] is False
    assert with_costs["paper_equity"] < without_costs["paper_equity"]
    assert all(row["entry_cost"] > 0 for row in with_costs["selected"])
    assert all(row["entry_cost"] == 0 for row in without_costs["selected"])
