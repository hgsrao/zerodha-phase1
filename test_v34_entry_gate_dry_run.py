import json
from decimal import Decimal
from pathlib import Path

import pytest

import entry_gate_dry_run as egdr
from entry_gate_dry_run import (
    _DEFAULT_CONFIG,
    REWARD_RISK_MULTIPLE,
    build_authorizer,
    build_candidate_order,
    compute_emergency_exit_trigger,
    compute_hypothetical_take_profit,
    compute_realized_hypothetical_pnl,
    evaluate_against_real_risk_gate,
    evaluate_hypothetical_exit,
    evaluate_hypothetical_take_profit,
    get_cached_tick_size,
    read_latest_candidates,
    record_entry_price,
    run_once,
    validate_exit_payload,
    validate_take_profit_payload,
)
from institutional_engine_v34_p01d_candidate import BrokerRiskSnapshot


class FakeBroker:
    """Matches KiteBrokerAdapter.get_daily_risk_snapshot()'s contract via a
    hand-built snapshot - only RunnerEntryAuthorizer/P03RiskController
    themselves need to be the real, unmodified production classes."""

    def __init__(self, snapshot, tick_size=Decimal("0.05")):
        self._snapshot = snapshot
        self._tick_size = tick_size
        self.tick_size_calls = []

    def get_daily_risk_snapshot(self):
        return self._snapshot

    def get_tick_size(self, symbol):
        self.tick_size_calls.append(symbol)
        return self._tick_size


@pytest.fixture(autouse=True)
def _clear_tick_size_cache():
    """get_cached_tick_size() caches across the whole process lifetime by
    design (see its docstring) - tests must not leak cache entries between
    each other, since different tests use the same symbols with different
    fake tick sizes."""
    egdr._TICK_SIZE_CACHE.clear()
    yield
    egdr._TICK_SIZE_CACHE.clear()


def make_snapshot(**overrides):
    defaults = dict(
        realized_pnl=Decimal("0"), unrealized_pnl=Decimal("0"), charges=Decimal("0"),
        deployed_capital=Decimal("0"), pending_buy_exposure=Decimal("0"),
        capital_limit=_DEFAULT_CONFIG.trial_capital, daily_loss_limit=Decimal("2000"),
    )
    defaults.update(overrides)
    return BrokerRiskSnapshot(**defaults)


class TestBuildCandidateOrder:
    def test_builds_the_real_dispatch_shape(self):
        order = build_candidate_order("INFY", 500.0, 10)
        assert order["tag"] == "V3.4_ENTRY"
        assert order["product"] == "MIS"
        assert order["quantity"] == 10

    def test_returns_none_for_non_positive_price_or_quantity(self):
        assert build_candidate_order("INFY", 0.0, 10) is None
        assert build_candidate_order("INFY", 500.0, 0) is None


class TestReadLatestCandidates:
    def test_no_files_present_returns_empty(self, tmp_path):
        result = read_latest_candidates(
            momentum_path=tmp_path / "missing1.json", orb_path=tmp_path / "missing2.json",
        )
        assert result == []

    def test_extracts_momentum_candidate_when_marked(self, tmp_path):
        momentum_path = tmp_path / "momentum.json"
        momentum_path.write_text(json.dumps({
            "external_variant": {"status": "MARKED", "selected": [{"symbol": "INFY", "live_price": 1500.0}]},
        }))
        result = read_latest_candidates(momentum_path=momentum_path, orb_path=tmp_path / "missing.json")
        assert result == [{"source": "EXTERNAL_CROSS_SECTIONAL_12_1", "symbol": "INFY", "price": 1500.0}]

    def test_extracts_orb_candidate_on_hypothetical_buy(self, tmp_path):
        orb_path = tmp_path / "orb.json"
        orb_path.write_text(json.dumps({
            "decision": "HYPOTHETICAL_BUY", "top_candidate": {"symbol": "TCS", "last_price": 3800.0},
        }))
        result = read_latest_candidates(momentum_path=tmp_path / "missing.json", orb_path=orb_path)
        assert result == [{"source": "ORB_EXPLORATORY", "symbol": "TCS", "price": 3800.0}]

    def test_malformed_json_is_treated_as_absent(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not valid json")
        result = read_latest_candidates(momentum_path=path, orb_path=tmp_path / "missing.json")
        assert result == []


class TestEvaluateAgainstRealRiskGateFullPipeline:
    def test_allows_a_clean_candidate_reaching_the_end(self, tmp_path):
        broker = FakeBroker(make_snapshot())
        authorizer = build_authorizer(broker, state_path=tmp_path / "isolated.json")
        snapshot = authorizer._fresh_snapshot()
        result = evaluate_against_real_risk_gate(authorizer=authorizer, broker=broker, symbol="INFY", price=500.0, snapshot=snapshot)
        assert result["risk_gate"]["allowed"] is True
        assert result["risk_gate"]["final_stage_reached"] == "ALL_CONDITIONS_PASSED"
        assert result["risk_gate"]["quantity"] > 0

    def test_blocks_on_capital_ceiling_shallow_gate(self, tmp_path):
        broker = FakeBroker(make_snapshot(deployed_capital=_DEFAULT_CONFIG.trial_capital))
        authorizer = build_authorizer(broker, state_path=tmp_path / "isolated.json")
        snapshot = authorizer._fresh_snapshot()
        result = evaluate_against_real_risk_gate(authorizer=authorizer, broker=broker, symbol="INFY", price=500.0, snapshot=snapshot)
        assert result["risk_gate"]["allowed"] is False
        assert result["risk_gate"]["final_stage_reached"] == "CAPITAL_AND_DAILY_LOSS_GATE"
        assert result["risk_gate"]["reason"] == "CAPITAL_CEILING"

    def test_blocks_on_simultaneous_position_limit_deep_gate(self, tmp_path):
        """This is exactly the gate the first (shallow) version of this tool
        could not see at all - it only ever checked P03RiskController, not
        RunnerEntryAuthorizer's own policy layer."""
        broker = FakeBroker(make_snapshot(pending_buy_exposure=Decimal("1")))
        authorizer = build_authorizer(broker, state_path=tmp_path / "isolated.json")
        snapshot = authorizer._fresh_snapshot()
        result = evaluate_against_real_risk_gate(authorizer=authorizer, broker=broker, symbol="INFY", price=500.0, snapshot=snapshot)
        assert result["risk_gate"]["allowed"] is False
        assert result["risk_gate"]["final_stage_reached"] == "P0_3B_F_POLICY_GATE"
        assert "SIMULTANEOUS_POSITION_LIMIT" in result["risk_gate"]["reason"]

    def test_blocks_on_daily_hard_halt_deep_gate(self, tmp_path):
        broker = FakeBroker(make_snapshot(realized_pnl=-(_DEFAULT_CONFIG.trial_capital * _DEFAULT_CONFIG.daily_hard_halt_pct)))
        authorizer = build_authorizer(broker, state_path=tmp_path / "isolated.json")
        snapshot = authorizer._fresh_snapshot()
        result = evaluate_against_real_risk_gate(authorizer=authorizer, broker=broker, symbol="INFY", price=500.0, snapshot=snapshot)
        assert result["risk_gate"]["allowed"] is False
        assert result["risk_gate"]["final_stage_reached"] == "P0_3B_F_POLICY_GATE"
        assert "DAILY_HARD_HALT" in result["risk_gate"]["reason"]

    def test_second_entry_same_day_blocked_by_daily_entry_limit(self, tmp_path):
        """Proves reservations persist across calls in the isolated state,
        faithfully simulating a real trading day."""
        state_path = tmp_path / "isolated.json"
        broker1 = FakeBroker(make_snapshot())
        authorizer1 = build_authorizer(broker1, state_path=state_path)
        first = evaluate_against_real_risk_gate(
            authorizer=authorizer1, broker=broker1, symbol="INFY", price=500.0, snapshot=authorizer1._fresh_snapshot(),
        )
        assert first["risk_gate"]["allowed"] is True

        # Fresh authorizer instance (new poll cycle), same isolated state file,
        # broker still shows no deployed capital (position hasn't "filled" -
        # this dry run never simulates a fill) - so only the entry-limit
        # counter, not the simultaneous-position check, should now bind.
        broker2 = FakeBroker(make_snapshot())
        authorizer2 = build_authorizer(broker2, state_path=state_path)
        second = evaluate_against_real_risk_gate(
            authorizer=authorizer2, broker=broker2, symbol="TCS", price=3000.0, snapshot=authorizer2._fresh_snapshot(),
        )
        assert second["risk_gate"]["allowed"] is False
        assert "DAILY_ENTRY_LIMIT" in second["risk_gate"]["reason"] or "ENTRY_COOLDOWN" in second["risk_gate"]["reason"]

    def test_one_shared_snapshot_serves_every_candidate_in_a_cycle(self, tmp_path):
        """The bug this fixed: re-fetching per candidate multiplied broker
        calls for no benefit and occasionally hit a transient failure on a
        later candidate in the same cycle. A single snapshot object reused
        across evaluate_against_real_risk_gate calls must work identically
        to giving each one its own fetch would have."""
        broker = FakeBroker(make_snapshot())
        authorizer = build_authorizer(broker, state_path=tmp_path / "isolated.json")
        snapshot = authorizer._fresh_snapshot()
        first = evaluate_against_real_risk_gate(authorizer=authorizer, broker=broker, symbol="INFY", price=500.0, snapshot=snapshot)
        second = evaluate_against_real_risk_gate(authorizer=authorizer, broker=broker, symbol="TCS", price=3000.0, snapshot=snapshot)
        assert first["risk_gate"]["allowed"] is True
        # Second candidate in the same cycle, same shared snapshot: blocked
        # by the real cooldown/one-at-a-time policy, not by anything wrong
        # with the snapshot-sharing itself.
        assert second["risk_gate"]["allowed"] is False
        assert second["risk_gate"]["final_stage_reached"] == "P0_3B_F_POLICY_GATE"

    def test_physical_dispatch_gate_is_unconditional_even_when_allowed(self, tmp_path):
        broker = FakeBroker(make_snapshot())
        authorizer = build_authorizer(broker, state_path=tmp_path / "isolated.json")
        snapshot = authorizer._fresh_snapshot()
        result = evaluate_against_real_risk_gate(authorizer=authorizer, broker=broker, symbol="INFY", price=500.0, snapshot=snapshot)
        assert result["risk_gate"]["allowed"] is True
        assert result["physical_dispatch_gate"]["status"] == "BLOCKED_LIVE_TRADING_DISABLED"
        assert result["physical_dispatch_gate"]["live_trading_enabled"] is False
        assert result["capabilities"]["order_api"] is False

    def test_refuses_to_run_if_somehow_asked_for_live_trading(self, tmp_path):
        broker = FakeBroker(make_snapshot())
        authorizer = build_authorizer(broker, state_path=tmp_path / "isolated.json")
        snapshot = authorizer._fresh_snapshot()
        with pytest.raises(RuntimeError, match="dry-run only"):
            evaluate_against_real_risk_gate(
                authorizer=authorizer, broker=broker, symbol="INFY", price=500.0, snapshot=snapshot, live_trading_enabled=True,
            )

    def test_isolated_state_never_touches_real_production_files(self, tmp_path):
        state_path = tmp_path / "isolated.json"
        broker = FakeBroker(make_snapshot())
        authorizer = build_authorizer(broker, state_path=state_path)
        snapshot = authorizer._fresh_snapshot()
        evaluate_against_real_risk_gate(authorizer=authorizer, broker=broker, symbol="INFY", price=500.0, snapshot=snapshot)
        assert state_path.exists()
        assert not (tmp_path / "bot_state_v34.json").exists()
        assert not (tmp_path / "bot_state_v34_p03_entry_control.json").exists()

    def test_includes_hypothetical_exit_when_allowed(self, tmp_path):
        broker = FakeBroker(make_snapshot(), tick_size=Decimal("0.05"))
        authorizer = build_authorizer(broker, state_path=tmp_path / "isolated.json")
        snapshot = authorizer._fresh_snapshot()
        result = evaluate_against_real_risk_gate(authorizer=authorizer, broker=broker, symbol="INFY", price=500.0, snapshot=snapshot)
        assert result["risk_gate"]["allowed"] is True
        exit_info = result["hypothetical_exit"]
        assert exit_info is not None
        assert exit_info["valid"] is True
        assert exit_info["order_payload"]["transaction_type"] == "SELL"
        assert exit_info["order_payload"]["tag"] == "V3.4_EXIT"
        assert Decimal(exit_info["order_payload"]["trigger_price"]) < Decimal("500.0")

    def test_hypothetical_exit_is_none_when_blocked(self, tmp_path):
        broker = FakeBroker(make_snapshot(deployed_capital=_DEFAULT_CONFIG.trial_capital))
        authorizer = build_authorizer(broker, state_path=tmp_path / "isolated.json")
        snapshot = authorizer._fresh_snapshot()
        result = evaluate_against_real_risk_gate(authorizer=authorizer, broker=broker, symbol="INFY", price=500.0, snapshot=snapshot)
        assert result["risk_gate"]["allowed"] is False
        assert result["hypothetical_exit"] is None

    def test_includes_hypothetical_take_profit_when_allowed(self, tmp_path):
        broker = FakeBroker(make_snapshot(), tick_size=Decimal("0.05"))
        authorizer = build_authorizer(broker, state_path=tmp_path / "isolated.json")
        snapshot = authorizer._fresh_snapshot()
        result = evaluate_against_real_risk_gate(authorizer=authorizer, broker=broker, symbol="INFY", price=500.0, snapshot=snapshot)
        assert result["risk_gate"]["allowed"] is True
        tp_info = result["hypothetical_take_profit"]
        assert tp_info is not None
        assert tp_info["valid"] is True
        # entry=500.0, trigger=499.95 (one tick) -> risk_per_share=0.05,
        # target = 500.0 + 2.0*0.05 = 500.10, same 2:1 ratio the entry-price
        # question this whole feature answers.
        assert tp_info["order_payload"]["price"] == "500.10"
        assert tp_info["order_payload"]["transaction_type"] == "SELL"
        assert tp_info["order_payload"]["tag"] == "V3.4_HYPOTHETICAL_TP_NOT_NATIVE"

    def test_hypothetical_take_profit_is_none_when_blocked(self, tmp_path):
        broker = FakeBroker(make_snapshot(deployed_capital=_DEFAULT_CONFIG.trial_capital))
        authorizer = build_authorizer(broker, state_path=tmp_path / "isolated.json")
        snapshot = authorizer._fresh_snapshot()
        result = evaluate_against_real_risk_gate(authorizer=authorizer, broker=broker, symbol="INFY", price=500.0, snapshot=snapshot)
        assert result["risk_gate"]["allowed"] is False
        assert result["hypothetical_take_profit"] is None


class TestComputeEmergencyExitTrigger:
    def test_matches_the_real_engine_formula_exactly(self):
        # institutional_engine_v34_p01d_candidate.py: floor((ltp - tick)/tick)*tick
        trigger = compute_emergency_exit_trigger(Decimal("500.00"), Decimal("0.05"))
        assert trigger == Decimal("499.95")

    def test_rounds_down_to_the_nearest_tick_when_ltp_is_not_tick_aligned(self):
        trigger = compute_emergency_exit_trigger(Decimal("500.03"), Decimal("0.05"))
        # raw = 499.98 -> floor to nearest 0.05 = 499.95
        assert trigger == Decimal("499.95")


class TestValidateExitPayload:
    def _valid_kwargs(self, **overrides):
        kwargs = dict(
            symbol="INFY", quantity=10, trigger_price=Decimal("499.95"),
            source_ltp=Decimal("500.0"), tick_size=Decimal("0.05"),
        )
        kwargs.update(overrides)
        return kwargs

    def test_accepts_a_valid_payload(self):
        validate_exit_payload(**self._valid_kwargs())  # must not raise

    def test_rejects_empty_symbol(self):
        with pytest.raises(RuntimeError, match="symbol is invalid"):
            validate_exit_payload(**self._valid_kwargs(symbol=""))

    def test_rejects_symbol_with_surrounding_whitespace(self):
        with pytest.raises(RuntimeError, match="surrounding whitespace"):
            validate_exit_payload(**self._valid_kwargs(symbol=" INFY "))

    def test_rejects_non_positive_quantity(self):
        with pytest.raises(RuntimeError, match="positive integer"):
            validate_exit_payload(**self._valid_kwargs(quantity=0))

    def test_rejects_non_positive_ltp(self):
        with pytest.raises(RuntimeError, match="source LTP must be positive"):
            validate_exit_payload(**self._valid_kwargs(source_ltp=Decimal("0")))

    def test_rejects_trigger_not_strictly_below_ltp(self):
        with pytest.raises(RuntimeError, match="strictly below source LTP"):
            validate_exit_payload(**self._valid_kwargs(trigger_price=Decimal("500.0")))

    def test_rejects_a_trigger_not_aligned_to_tick_size(self):
        with pytest.raises(RuntimeError, match="not aligned to instrument tick size"):
            validate_exit_payload(**self._valid_kwargs(trigger_price=Decimal("499.97")))

    def test_rejects_market_protection_other_than_frozen_value(self):
        with pytest.raises(RuntimeError, match="frozen to -1"):
            validate_exit_payload(**self._valid_kwargs(market_protection=Decimal("0")))

    def test_rejects_the_wrong_tag(self):
        with pytest.raises(RuntimeError, match="Invalid V3.4 emergency exit tag"):
            validate_exit_payload(**self._valid_kwargs(tag="WRONG_TAG"))


class TestComputeHypotheticalTakeProfit:
    def test_2to1_reward_risk_matches_by_hand(self):
        # entry=500.00, trigger=499.95 -> risk=0.05 -> target=500.00+2*0.05=500.10
        target = compute_hypothetical_take_profit(Decimal("500.00"), Decimal("499.95"), Decimal("0.05"))
        assert target == Decimal("500.10")

    def test_default_multiple_is_2point0_matching_p02_pillar_ii_convention(self):
        assert REWARD_RISK_MULTIPLE == Decimal("2.0")

    def test_rounds_down_to_the_nearest_tick(self):
        # entry=500.03, trigger=499.98 -> risk=0.05 -> raw target=500.13
        # 500.13 / 0.05 = 10002.6 -> floor to 10002 -> 500.10
        target = compute_hypothetical_take_profit(Decimal("500.03"), Decimal("499.98"), Decimal("0.05"))
        assert target == Decimal("500.10")

    def test_custom_reward_risk_multiple_is_honored(self):
        target = compute_hypothetical_take_profit(
            Decimal("500.00"), Decimal("499.95"), Decimal("0.05"), reward_risk_multiple=Decimal("3.0"),
        )
        assert target == Decimal("500.15")


class TestValidateTakeProfitPayload:
    def _valid_kwargs(self, **overrides):
        kwargs = dict(
            symbol="INFY", quantity=10, target_price=Decimal("500.10"),
            entry_price=Decimal("500.0"), tick_size=Decimal("0.05"),
        )
        kwargs.update(overrides)
        return kwargs

    def test_accepts_a_valid_payload(self):
        validate_take_profit_payload(**self._valid_kwargs())  # must not raise

    def test_rejects_empty_symbol(self):
        with pytest.raises(RuntimeError, match="symbol is invalid"):
            validate_take_profit_payload(**self._valid_kwargs(symbol=""))

    def test_rejects_symbol_with_surrounding_whitespace(self):
        with pytest.raises(RuntimeError, match="surrounding whitespace"):
            validate_take_profit_payload(**self._valid_kwargs(symbol=" INFY "))

    def test_rejects_non_positive_quantity(self):
        with pytest.raises(RuntimeError, match="positive integer"):
            validate_take_profit_payload(**self._valid_kwargs(quantity=0))

    def test_rejects_non_positive_entry_price(self):
        with pytest.raises(RuntimeError, match="entry price must be positive"):
            validate_take_profit_payload(**self._valid_kwargs(entry_price=Decimal("0")))

    def test_rejects_target_not_strictly_above_entry(self):
        with pytest.raises(RuntimeError, match="strictly above entry price"):
            validate_take_profit_payload(**self._valid_kwargs(target_price=Decimal("500.0")))

    def test_rejects_a_target_not_aligned_to_tick_size(self):
        with pytest.raises(RuntimeError, match="not aligned to instrument tick size"):
            validate_take_profit_payload(**self._valid_kwargs(target_price=Decimal("500.07")))

    def test_rejects_the_wrong_tag(self):
        with pytest.raises(RuntimeError, match="Invalid hypothetical take-profit tag"):
            validate_take_profit_payload(**self._valid_kwargs(tag="WRONG_TAG"))


class TestEvaluateHypotheticalTakeProfit:
    def test_builds_a_valid_sell_limit_with_the_2to1_target(self):
        broker = FakeBroker(make_snapshot(), tick_size=Decimal("0.05"))
        result = evaluate_hypothetical_take_profit(
            broker, symbol="INFY", quantity=10, entry_price=500.0, emergency_exit_trigger="499.95",
        )
        assert result["valid"] is True
        assert result["reason"] is None
        assert result["order_payload"]["price"] == "500.10"
        assert result["order_payload"]["transaction_type"] == "SELL"
        assert result["order_payload"]["order_type"] == "LIMIT"
        assert result["order_payload"]["tag"] == "V3.4_HYPOTHETICAL_TP_NOT_NATIVE"

    def test_reports_tick_size_fetch_failure_without_raising(self):
        class BrokenTickSizeBroker:
            def get_tick_size(self, symbol):
                raise RuntimeError("FAIL_CLOSED: instrument lookup failed")

        result = evaluate_hypothetical_take_profit(
            BrokenTickSizeBroker(), symbol="INFY", quantity=10, entry_price=500.0, emergency_exit_trigger="499.95",
        )
        assert result["valid"] is False
        assert result["order_payload"] is None
        assert "TICK_SIZE_UNAVAILABLE" in result["reason"]


class TestGetCachedTickSize:
    def test_caches_across_repeated_calls_for_the_same_symbol(self):
        broker = FakeBroker(make_snapshot(), tick_size=Decimal("0.05"))
        first = get_cached_tick_size(broker, "INFY")
        second = get_cached_tick_size(broker, "INFY")
        assert first == second == Decimal("0.05")
        assert broker.tick_size_calls == ["INFY"]  # only fetched once

    def test_fetches_separately_per_distinct_symbol(self):
        broker = FakeBroker(make_snapshot(), tick_size=Decimal("0.05"))
        get_cached_tick_size(broker, "INFY")
        get_cached_tick_size(broker, "TCS")
        assert broker.tick_size_calls == ["INFY", "TCS"]


class TestEvaluateHypotheticalExit:
    def test_builds_a_valid_sell_order_with_the_real_trigger_formula(self):
        broker = FakeBroker(make_snapshot(), tick_size=Decimal("0.05"))
        result = evaluate_hypothetical_exit(broker, symbol="INFY", quantity=10, source_ltp=500.0)
        assert result["valid"] is True
        assert result["reason"] is None
        assert result["order_payload"]["trigger_price"] == "499.95"
        assert result["order_payload"]["transaction_type"] == "SELL"
        assert result["order_payload"]["order_type"] == "SL-M"

    def test_reports_tick_size_fetch_failure_without_raising(self):
        class BrokenTickSizeBroker:
            def get_tick_size(self, symbol):
                raise RuntimeError("FAIL_CLOSED: instrument lookup failed")

        result = evaluate_hypothetical_exit(BrokenTickSizeBroker(), symbol="INFY", quantity=10, source_ltp=500.0)
        assert result["valid"] is False
        assert result["order_payload"] is None
        assert "TICK_SIZE_UNAVAILABLE" in result["reason"]


class CountingFakeBroker:
    def __init__(self, snapshot):
        self._snapshot = snapshot
        self.calls = 0

    def get_daily_risk_snapshot(self):
        self.calls += 1
        return self._snapshot

    def get_tick_size(self, symbol):
        return Decimal("0.05")


class FailingFakeBroker:
    def get_daily_risk_snapshot(self):
        raise RuntimeError("simulated transient broker failure")


class TestRecordEntryPrice:
    def test_creates_the_log_and_appends_an_entry(self, tmp_path):
        log_path = tmp_path / "position_log.json"
        record_entry_price(
            log_path, trading_day="2026-08-14", symbol="LAURUSLABS", quantity=13,
            entry_price=1814.8, timestamp="2026-08-14T08:45:54+05:30",
            source="EXTERNAL_CROSS_SECTIONAL_12_1",
        )
        log = json.loads(log_path.read_text())
        assert log["2026-08-14"] == [{
            "symbol": "LAURUSLABS", "quantity": 13, "entry_price": 1814.8,
            "timestamp": "2026-08-14T08:45:54+05:30", "source": "EXTERNAL_CROSS_SECTIONAL_12_1",
        }]

    def test_appends_a_second_entry_on_the_same_day_without_losing_the_first(self, tmp_path):
        log_path = tmp_path / "position_log.json"
        record_entry_price(log_path, trading_day="2026-08-14", symbol="LAURUSLABS", quantity=13,
                           entry_price=1814.8, timestamp="t1", source="A")
        record_entry_price(log_path, trading_day="2026-08-14", symbol="BAJFINANCE", quantity=22,
                           entry_price=1112.6, timestamp="t2", source="A")
        log = json.loads(log_path.read_text())
        assert [row["symbol"] for row in log["2026-08-14"]] == ["LAURUSLABS", "BAJFINANCE"]

    def test_keeps_separate_days_separate(self, tmp_path):
        log_path = tmp_path / "position_log.json"
        record_entry_price(log_path, trading_day="2026-08-13", symbol="INFY", quantity=5,
                           entry_price=1500.0, timestamp="t1", source="A")
        record_entry_price(log_path, trading_day="2026-08-14", symbol="TCS", quantity=3,
                           entry_price=3000.0, timestamp="t2", source="A")
        log = json.loads(log_path.read_text())
        assert set(log) == {"2026-08-13", "2026-08-14"}
        assert len(log["2026-08-13"]) == 1
        assert len(log["2026-08-14"]) == 1


class TestComputeRealizedHypotheticalPnl:
    def test_computes_exact_net_pnl_with_real_costs_on_both_legs(self, tmp_path):
        log_path = tmp_path / "position_log.json"
        record_entry_price(log_path, trading_day="2026-08-14", symbol="LAURUSLABS", quantity=13,
                           entry_price=1814.8, timestamp="t1", source="A")
        result = compute_realized_hypothetical_pnl(
            log_path, trading_day="2026-08-14", current_prices={"LAURUSLABS": 1787.8},
        )
        assert result["trading_day"] == "2026-08-14"
        assert len(result["positions"]) == 1
        position = result["positions"][0]
        assert position["status"] == "MARKED"
        # Sanity: net P&L should be a loss (price dropped) and roughly match
        # the raw price move minus a few tens of rupees of real costs.
        raw_move = (1787.8 - 1814.8) * 13
        assert Decimal(position["net_pnl"]) < Decimal(str(raw_move))  # costs make it worse, not better
        assert Decimal(position["net_pnl"]) > Decimal(str(raw_move)) - Decimal("100")  # but not wildly so
        assert Decimal(result["total_net_pnl"]) == Decimal(position["net_pnl"])

    def test_position_with_no_current_price_is_reported_but_excluded_from_total(self, tmp_path):
        log_path = tmp_path / "position_log.json"
        record_entry_price(log_path, trading_day="2026-08-14", symbol="LAURUSLABS", quantity=13,
                           entry_price=1814.8, timestamp="t1", source="A")
        result = compute_realized_hypothetical_pnl(
            log_path, trading_day="2026-08-14", current_prices={},
        )
        assert result["positions"][0]["status"] == "PRICE_UNAVAILABLE"
        assert result["positions"][0]["net_pnl"] is None
        assert Decimal(result["total_net_pnl"]) == Decimal("0")

    def test_empty_day_returns_empty_positions_and_zero_total(self, tmp_path):
        log_path = tmp_path / "position_log.json"
        result = compute_realized_hypothetical_pnl(
            log_path, trading_day="2026-08-14", current_prices={"INFY": 1500.0},
        )
        assert result["positions"] == []
        assert Decimal(result["total_net_pnl"]) == Decimal("0")

    def test_sums_multiple_positions_correctly(self, tmp_path):
        log_path = tmp_path / "position_log.json"
        record_entry_price(log_path, trading_day="2026-08-14", symbol="LAURUSLABS", quantity=13,
                           entry_price=1814.8, timestamp="t1", source="A")
        record_entry_price(log_path, trading_day="2026-08-14", symbol="BAJFINANCE", quantity=22,
                           entry_price=1112.6, timestamp="t2", source="A")
        result = compute_realized_hypothetical_pnl(
            log_path, trading_day="2026-08-14",
            current_prices={"LAURUSLABS": 1787.8, "BAJFINANCE": 1086.0},
        )
        individual_sum = sum(Decimal(p["net_pnl"]) for p in result["positions"])
        assert Decimal(result["total_net_pnl"]) == individual_sum


class TestRunOnceRecordsEntryPrices:
    def test_records_the_exact_price_when_a_candidate_is_allowed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        Path("shadow_strategy_telemetry.json").write_text(json.dumps({
            "external_variant": {"status": "MARKED", "selected": [{"symbol": "INFY", "live_price": 500.0}]},
        }))
        run_once(
            broker=FakeBroker(make_snapshot()), output=Path("out.json"),
            state_path=Path("isolated.json"), position_log_path=Path("positions.json"),
        )
        log = json.loads(Path("positions.json").read_text())
        today = list(log.values())[0]
        assert today == [{"symbol": "INFY", "quantity": 50, "entry_price": 500.0,
                          "timestamp": today[0]["timestamp"], "source": "EXTERNAL_CROSS_SECTIONAL_12_1"}]

    def test_does_not_record_a_blocked_candidate(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        Path("shadow_strategy_telemetry.json").write_text(json.dumps({
            "external_variant": {"status": "MARKED", "selected": [{"symbol": "INFY", "live_price": 500.0}]},
        }))
        broker = FakeBroker(make_snapshot(deployed_capital=_DEFAULT_CONFIG.trial_capital))
        run_once(
            broker=broker, output=Path("out.json"),
            state_path=Path("isolated.json"), position_log_path=Path("positions.json"),
        )
        assert not Path("positions.json").exists()


class TestRunOnce:
    def test_writes_output_and_uses_isolated_state(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        Path("shadow_strategy_telemetry.json").write_text(json.dumps({
            "external_variant": {"status": "MARKED", "selected": [{"symbol": "INFY", "live_price": 500.0}]},
        }))
        telemetry = run_once(
            broker=FakeBroker(make_snapshot()), output=Path("out.json"), state_path=Path("isolated.json"),
        )
        assert telemetry["candidates_found"] == 1
        assert telemetry["evaluations"][0]["risk_gate"]["allowed"] is True
        assert Path("out.json").exists()
        assert Path("isolated.json").exists()
        assert not Path("bot_state_v34.json").exists()

    def test_no_candidates_produces_empty_evaluations(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        telemetry = run_once(
            broker=FakeBroker(make_snapshot()), output=Path("out.json"), state_path=Path("isolated.json"),
        )
        assert telemetry["candidates_found"] == 0
        assert telemetry["evaluations"] == []

    def test_fetches_the_snapshot_exactly_once_regardless_of_candidate_count(self, tmp_path, monkeypatch):
        """The actual bug fix: four candidates in one cycle used to mean four
        separate broker snapshot fetches; now it's exactly one, shared."""
        monkeypatch.chdir(tmp_path)
        Path("shadow_strategy_telemetry.json").write_text(json.dumps({
            "external_variant": {"status": "MARKED", "selected": [
                {"symbol": "INFY", "live_price": 500.0},
                {"symbol": "TCS", "live_price": 3000.0},
                {"symbol": "SBIN", "live_price": 600.0},
                {"symbol": "SUNPHARMA", "live_price": 1500.0},
            ]},
        }))
        broker = CountingFakeBroker(make_snapshot())
        telemetry = run_once(broker=broker, output=Path("out.json"), state_path=Path("isolated.json"))
        assert telemetry["candidates_found"] == 4
        assert broker.calls == 1

    def test_snapshot_fetch_failure_fails_closed_for_every_candidate_cleanly(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        Path("shadow_strategy_telemetry.json").write_text(json.dumps({
            "external_variant": {"status": "MARKED", "selected": [
                {"symbol": "INFY", "live_price": 500.0},
                {"symbol": "TCS", "live_price": 3000.0},
            ]},
        }))
        telemetry = run_once(broker=FailingFakeBroker(), output=Path("out.json"), state_path=Path("isolated.json"))
        assert telemetry["candidates_found"] == 2
        assert len(telemetry["evaluations"]) == 2
        for evaluation in telemetry["evaluations"]:
            assert evaluation["risk_gate"]["allowed"] is False
            assert evaluation["risk_gate"]["final_stage_reached"] == "BROKER_SNAPSHOT"
            assert evaluation["physical_dispatch_gate"]["status"] == "BLOCKED_LIVE_TRADING_DISABLED"
        assert Path("out.json").exists()


def test_source_contains_no_order_or_production_state_operations():
    source = Path("entry_gate_dry_run.py").read_text(encoding="utf-8")
    forbidden = (
        ".place_order(", ".modify_order(", ".cancel_order(",
        "request_entry(", '"bot_state_v34.json"', "'bot_state_v34.json'",
    )
    assert all(token not in source for token in forbidden)


def test_capital_limit_matches_the_real_production_config_default():
    from institutional_engine_v34_p01d_candidate import Config
    assert _DEFAULT_CONFIG.trial_capital == Config.__dataclass_fields__["trial_capital"].default
