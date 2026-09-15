"""Tests for v34_p02_state.py (P02-B) - data structures and fail-closed
(de)serialization only. No broker, no reconciliation, no strategy logic
is exercised here, matching P02-B's frozen scope."""

from decimal import Decimal

import pytest

from v34_p02_state import (
    BotState,
    Config,
    ControlClass,
    DailyAccountingCheckpoint,
    EngineStatus,
    EntryPolicyDeclinedError,
    EntryReservation,
    PositionStatus,
    StateIntegrityError,
    TradeContext,
)


class TestEntryPolicyDeclinedError:
    def test_carries_its_reason(self):
        exc = EntryPolicyDeclinedError("DAILY_HARD_HALT")
        assert exc.reason == "DAILY_HARD_HALT"
        assert str(exc) == "DAILY_HARD_HALT"

    def test_is_not_a_state_integrity_error(self):
        # Must never be caught by a handler written for the fail-closed
        # deserialization errors above - they are unrelated failure modes.
        assert not issubclass(EntryPolicyDeclinedError, StateIntegrityError)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TestEnums:
    def test_engine_status_has_exactly_the_four_spec_values(self):
        assert {s.value for s in EngineStatus} == {
            "STARTUP", "RECONCILING", "RUNNING", "RECONCILIATION_HALT",
        }

    def test_position_status_includes_the_abandoned_policy_halt_outcome(self):
        assert PositionStatus.ENTRY_ABANDONED_POLICY_HALT.value == "ENTRY_ABANDONED_POLICY_HALT"

    def test_control_class_has_exactly_the_three_spec_2_classes(self):
        assert {c.value for c in ControlClass} == {"ENTRY_LOCK", "ENGINE_HALT", "KILL_SWITCH"}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_defaults_construct_and_default_product_is_cnc(self):
        cfg = Config(alert_webhook_url="https://example.invalid/hook")
        assert cfg.product == "CNC"
        assert cfg.max_simultaneous_positions == 6
        assert cfg.trial_capital == Decimal("100000")

    def test_rejects_product_outside_mis_or_cnc(self):
        with pytest.raises(ValueError):
            Config(alert_webhook_url="x", product="NRML")

    def test_rejects_zero_max_simultaneous_positions(self):
        with pytest.raises(ValueError):
            Config(alert_webhook_url="x", max_simultaneous_positions=0)

    def test_rejects_negative_observation_retry_budget(self):
        with pytest.raises(ValueError):
            Config(alert_webhook_url="x", observation_retry_budget=-1)

    def test_config_is_frozen(self):
        cfg = Config(alert_webhook_url="x")
        with pytest.raises(Exception):
            cfg.trial_capital = Decimal("1")


# ---------------------------------------------------------------------------
# TradeContext
# ---------------------------------------------------------------------------

def _sample_trade_context(**overrides) -> TradeContext:
    defaults = dict(
        symbol="RELIANCE",
        entry_tag="V3.4_P02_ENTRY",
        target_qty=10,
        tranche_qty=10,
        status=PositionStatus.MANAGING,
        filled_qty=10,
        pending_qty=0,
        entry_order_id="OID1",
        entry_price=Decimal("2500.50"),
        stop_order_id=None,
        exit_order_id=None,
        order_status="COMPLETE",
        executed_tranches=1,
        booked_pnl=Decimal("0"),
        avg_entry_price=Decimal("2500.50"),
        stop_loss_price=Decimal("0"),
        exit_reason=None,
        opened_trading_day="2026-08-14",
        prior_close_mtm=Decimal("125.00"),
        entry_max_observed_fill=10,
        entry_hwm_order_id="OID1",
        stop_max_observed_fill=0,
        stop_hwm_order_id=None,
        exit_max_observed_fill=0,
        exit_hwm_order_id=None,
        exit_submission_fingerprint=None,
        exit_reconciliation_failures=0,
        entry_submission_fingerprint={"exchange": "NSE", "tradingsymbol": "RELIANCE"},
        entry_reconciliation_failures=0,
    )
    defaults.update(overrides)
    return TradeContext(**defaults)


class TestTradeContextRoundTrip:
    def test_round_trip_preserves_every_field_exactly(self):
        ctx = _sample_trade_context()
        restored = TradeContext.from_dict(ctx.to_dict())
        assert restored == ctx

    def test_round_trip_preserves_decimal_precision_not_just_float_equality(self):
        ctx = _sample_trade_context(entry_price=Decimal("1814.80"), prior_close_mtm=Decimal("-0.01"))
        restored = TradeContext.from_dict(ctx.to_dict())
        assert restored.entry_price == Decimal("1814.80")
        assert restored.prior_close_mtm == Decimal("-0.01")

    def test_round_trip_preserves_none_optional_fields(self):
        ctx = _sample_trade_context(stop_order_id=None, exit_reason=None, entry_submission_fingerprint=None)
        restored = TradeContext.from_dict(ctx.to_dict())
        assert restored.stop_order_id is None
        assert restored.exit_reason is None
        assert restored.entry_submission_fingerprint is None

    def test_round_trip_preserves_status_enum(self):
        ctx = _sample_trade_context(status=PositionStatus.EXIT_PENDING)
        restored = TradeContext.from_dict(ctx.to_dict())
        assert restored.status is PositionStatus.EXIT_PENDING


class TestTradeContextFailClosed:
    def test_missing_required_field_raises(self):
        raw = _sample_trade_context().to_dict()
        del raw["entry_price"]
        with pytest.raises(StateIntegrityError, match="entry_price"):
            TradeContext.from_dict(raw)

    def test_malformed_decimal_raises(self):
        raw = _sample_trade_context().to_dict()
        raw["entry_price"] = "not-a-number"
        with pytest.raises(StateIntegrityError, match="entry_price"):
            TradeContext.from_dict(raw)

    def test_non_finite_decimal_raises(self):
        raw = _sample_trade_context().to_dict()
        raw["booked_pnl"] = "Infinity"
        with pytest.raises(StateIntegrityError, match="booked_pnl"):
            TradeContext.from_dict(raw)

    def test_invalid_status_value_raises(self):
        raw = _sample_trade_context().to_dict()
        raw["status"] = "SOMETHING_MADE_UP"
        with pytest.raises(StateIntegrityError, match="status"):
            TradeContext.from_dict(raw)

    def test_boolean_is_not_accepted_as_a_decimal(self):
        # bool is a subclass of int in Python; must not silently coerce.
        raw = _sample_trade_context().to_dict()
        raw["entry_price"] = True
        with pytest.raises(StateIntegrityError, match="entry_price"):
            TradeContext.from_dict(raw)

    def test_non_dict_raises(self):
        with pytest.raises(StateIntegrityError):
            TradeContext.from_dict(["not", "a", "dict"])

    def test_malformed_fingerprint_type_raises(self):
        raw = _sample_trade_context().to_dict()
        raw["entry_submission_fingerprint"] = "should-be-an-object"
        with pytest.raises(StateIntegrityError, match="entry_submission_fingerprint"):
            TradeContext.from_dict(raw)


# ---------------------------------------------------------------------------
# BotState
# ---------------------------------------------------------------------------

class TestBotStateRoundTrip:
    def test_round_trip_with_empty_active_trades(self):
        state = BotState(trading_day="2026-08-14", status=EngineStatus.RUNNING)
        restored = BotState.from_dict(state.to_dict())
        assert restored == state

    def test_round_trip_with_multiple_owned_positions(self):
        state = BotState(
            trading_day="2026-08-14",
            status=EngineStatus.RUNNING,
            active_trades={
                "RELIANCE": _sample_trade_context(symbol="RELIANCE"),
                "INFY": _sample_trade_context(symbol="INFY", entry_price=Decimal("1500")),
            },
        )
        restored = BotState.from_dict(state.to_dict())
        assert set(restored.active_trades) == {"RELIANCE", "INFY"}
        assert restored.active_trades["INFY"].entry_price == Decimal("1500")

    def test_round_trip_preserves_halt_fields(self):
        state = BotState(
            trading_day="2026-08-14",
            status=EngineStatus.RECONCILIATION_HALT,
            halt_reason="Reconciliation Failure: orphan position",
            halt_source="ENGINE",
            halted_at="2026-08-14T10:00:00+00:00",
            clearance_required=True,
        )
        restored = BotState.from_dict(state.to_dict())
        assert restored.clearance_required is True
        assert restored.halt_reason == state.halt_reason


class TestBotStateFailClosed:
    def test_active_trades_key_symbol_mismatch_raises(self):
        state = BotState(trading_day="2026-08-14", active_trades={"RELIANCE": _sample_trade_context(symbol="RELIANCE")})
        raw = state.to_dict()
        raw["active_trades"]["WRONG_KEY"] = raw["active_trades"].pop("RELIANCE")
        with pytest.raises(StateIntegrityError, match="disagrees with its own dict key"):
            BotState.from_dict(raw)

    def test_active_trades_not_a_dict_raises(self):
        state = BotState(trading_day="2026-08-14")
        raw = state.to_dict()
        raw["active_trades"] = ["RELIANCE"]
        with pytest.raises(StateIntegrityError, match="active_trades"):
            BotState.from_dict(raw)

    def test_malformed_nested_trade_context_propagates(self):
        state = BotState(trading_day="2026-08-14", active_trades={"RELIANCE": _sample_trade_context(symbol="RELIANCE")})
        raw = state.to_dict()
        del raw["active_trades"]["RELIANCE"]["entry_price"]
        with pytest.raises(StateIntegrityError, match="entry_price"):
            BotState.from_dict(raw)

    def test_clearance_required_must_be_a_real_boolean(self):
        state = BotState(trading_day="2026-08-14")
        raw = state.to_dict()
        raw["clearance_required"] = "true"
        with pytest.raises(StateIntegrityError, match="clearance_required"):
            BotState.from_dict(raw)

    def test_missing_trading_day_raises(self):
        state = BotState(trading_day="2026-08-14")
        raw = state.to_dict()
        del raw["trading_day"]
        with pytest.raises(StateIntegrityError, match="trading_day"):
            BotState.from_dict(raw)

    def test_invalid_engine_status_raises(self):
        state = BotState(trading_day="2026-08-14")
        raw = state.to_dict()
        raw["status"] = "FLAT"  # retired status, must not be silently accepted
        with pytest.raises(StateIntegrityError, match="status"):
            BotState.from_dict(raw)


# ---------------------------------------------------------------------------
# DailyAccountingCheckpoint
# ---------------------------------------------------------------------------

class TestDailyAccountingCheckpoint:
    def test_round_trip(self):
        checkpoint = DailyAccountingCheckpoint(
            trading_day="2026-08-14",
            prior_close_equity=Decimal("101250.00"),
            day_start_equity=Decimal("101250.00"),
            trial_high_water_mark=Decimal("103400.00"),
        )
        restored = DailyAccountingCheckpoint.from_dict(checkpoint.to_dict())
        assert restored == checkpoint

    def test_missing_prior_close_equity_raises_rather_than_defaulting(self):
        # This is the exact scenario named in the frozen spec (P02-0 §12):
        # a malformed checkpoint must become an integrity failure, never
        # silently resolve to prior_close_equity = trial_capital.
        checkpoint = DailyAccountingCheckpoint(
            trading_day="2026-08-14",
            prior_close_equity=Decimal("101250.00"),
            day_start_equity=Decimal("101250.00"),
            trial_high_water_mark=Decimal("103400.00"),
        )
        raw = checkpoint.to_dict()
        del raw["prior_close_equity"]
        with pytest.raises(StateIntegrityError, match="prior_close_equity"):
            DailyAccountingCheckpoint.from_dict(raw)

    def test_malformed_hwm_raises(self):
        raw = {
            "trading_day": "2026-08-14",
            "prior_close_equity": "101250.00",
            "day_start_equity": "101250.00",
            "trial_high_water_mark": "not-a-decimal",
        }
        with pytest.raises(StateIntegrityError, match="trial_high_water_mark"):
            DailyAccountingCheckpoint.from_dict(raw)


# ---------------------------------------------------------------------------
# EntryReservation
# ---------------------------------------------------------------------------

class TestEntryReservation:
    def test_round_trip(self):
        reservation = EntryReservation(
            symbol="INFY",
            sector="IT",
            reserved_capital=Decimal("25000.00"),
            reserved_at="2026-08-14T09:20:00+00:00",
            entry_fingerprint={"tradingsymbol": "INFY", "quantity": 10},
        )
        restored = EntryReservation.from_dict(reservation.to_dict())
        assert restored == reservation

    def test_round_trip_with_no_fingerprint_yet(self):
        reservation = EntryReservation(
            symbol="INFY", sector="IT",
            reserved_capital=Decimal("25000.00"),
            reserved_at="2026-08-14T09:20:00+00:00",
        )
        restored = EntryReservation.from_dict(reservation.to_dict())
        assert restored.entry_fingerprint is None

    def test_missing_sector_raises(self):
        raw = {
            "symbol": "INFY",
            "reserved_capital": "25000.00",
            "reserved_at": "2026-08-14T09:20:00+00:00",
        }
        with pytest.raises(StateIntegrityError, match="sector"):
            EntryReservation.from_dict(raw)

    def test_malformed_reserved_capital_raises(self):
        raw = {
            "symbol": "INFY", "sector": "IT",
            "reserved_capital": "twenty-five-thousand",
            "reserved_at": "2026-08-14T09:20:00+00:00",
        }
        with pytest.raises(StateIntegrityError, match="reserved_capital"):
            EntryReservation.from_dict(raw)
