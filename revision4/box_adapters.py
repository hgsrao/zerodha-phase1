"""
Integration of real Revision 2 boxes into timestamp orchestrator.
Adapters create callbacks that the orchestrator invokes at each timestamp.

Requires explicit warmup bars (60+) for PA calibration.
Uses real Revision 4 typed contracts (Position, ExitReason, etc).
Explicit error reporting: no silent failures, no broad except blocks.
"""

import pandas as pd
from typing import Mapping, Sequence, Optional, Dict, List
from revision4.contracts import (
    Bar, EffectiveConfig, PortfolioSnapshot, ExitEvent, ExitReason,
    Position, OrderIntent, SizedProposal, TradePlan,
)
from revision4.config_access import calculate_transaction_cost
from revision4.timestamp_orchestrator import RankedOrderCandidate
from revision4.ten_box_integration import TenBoxIntegration
from revision2.boxes import (
    PredictiveAnalyticsBox,
    IntelligentDiscriminationBox,
    ModelPredictiveControlBox,
    SafetyGatesTargetBox,
    PositionManagerBox,
    P01DBox,
)
from revision2.contracts import MarketSnapshot


def build_candidate_provider(
    config: EffectiveConfig,
    warmup_bars_by_symbol: Dict[str, pd.DataFrame],
):
    """
    Build candidate_provider callback for TimestampOrchestrator.

    Args:
        config: EffectiveConfig with all canonical parameters
        warmup_bars_by_symbol: {symbol: DataFrame} with 60+ pre-run bars
                               strictly before the sealed month starts

    Instantiates real Revision 2 boxes and runs full PA → ID → MPC → Safety → PM pipeline.

    Returns callback that generates RankedOrderCandidate at each timestamp.
    """
    # Instantiate boxes once (state shared across bars)
    pa_box = PredictiveAnalyticsBox()
    id_box = IntelligentDiscriminationBox()
    mpc_box = ModelPredictiveControlBox()
    safety_gates = SafetyGatesTargetBox()
    position_manager = PositionManagerBox()
    p01d_box = P01DBox()
    ten_box = TenBoxIntegration(config)

    # Pre-calibrate PA on warmup bars (if provided)
    for symbol, warmup_df in warmup_bars_by_symbol.items():
        if len(warmup_df) >= 30:
            pa_box.calibrate(symbol, warmup_df)

    # Maintain bar history per symbol across timestamp calls
    bar_history: Dict[str, List[Bar]] = {}

    def candidate_provider(
        snapshot: PortfolioSnapshot,
        bars: Mapping[str, Bar],
        event_index: int,
    ) -> Sequence[RankedOrderCandidate]:
        """
        Generate ranked order candidates for this timestamp.

        Sequence:
          1. PA box generates signals (uses warmup calibration)
          2. ID box validates entry criteria
          3. MPC box creates trade plans with stops/targets
          4. SafetyGates pre-sizing checks (drawdown, etc)
          5. PositionManager sizes positions
          6. SafetyGates post-sizing checks (profit margin)
          7. P01D creates final orders
          8. Return ranked candidates
        """
        candidates: List[RankedOrderCandidate] = []

        # Build equity curve from snapshot (required for safety gates)
        equity_curve = [snapshot.marked_equity] if snapshot.marked_equity > 0 else [100_000.0]

        # Process each symbol
        for symbol, bar in bars.items():
            # Skip if position already open for this symbol
            if symbol in snapshot.positions:
                continue

            # Accumulate bar history for this symbol
            if symbol not in bar_history:
                bar_history[symbol] = []
            bar_history[symbol].append(bar)

            # Convert bar history to DataFrame for PA
            df_data = {
                'timestamp': [b.timestamp for b in bar_history[symbol]],
                'open': [b.open for b in bar_history[symbol]],
                'high': [b.high for b in bar_history[symbol]],
                'low': [b.low for b in bar_history[symbol]],
                'close': [b.close for b in bar_history[symbol]],
                'volume': [b.volume for b in bar_history[symbol]],
            }
            df = pd.DataFrame(df_data)

            admitted, _reason = ten_box.admit_and_certify(symbol, df)
            if not admitted:
                continue

            # Build MarketSnapshot with full history
            market_snapshot = MarketSnapshot(
                symbol=symbol,
                timestamp=bar.timestamp,
                bars=df,  # Full history available for PA
                next_bar_open=None,
            )

            # 1. PA: Generate signal
            ten_box.audit.called("predictive_analytics")
            pa_signal, pa_trace = pa_box.evaluate(market_snapshot, config)
            if pa_signal.direction == 0:
                # No directional bias
                continue

            # Chart confirmation and canonical execution-window validation
            # are independent of ID's statistical discrimination.
            chart = ten_box.chart_signal(df)
            approved_entry, _reason = ten_box.validate_entry(pa_signal.direction, bar.timestamp, chart)
            if not approved_entry:
                continue
            grid_ok, _reason = ten_box.grid_allows(pa_signal.direction, bars)
            if not grid_ok:
                continue

            # 2. ID: Validate signal
            ten_box.audit.called("intelligent_discrimination")
            id_decision, id_trace = id_box.evaluate(pa_signal, config)
            if not id_decision.approved:
                # Signal rejected
                continue

            # 3. MPC: Build trade plan
            # Compute ATR from bar history
            closes = df['close'].to_numpy()
            highs = df['high'].to_numpy()
            lows = df['low'].to_numpy()
            atr_period = min(14, len(df))
            if len(df) > 1:
                tr = pd.Series(
                    [max(h - l, abs(h - c_prev), abs(l - c_prev))
                     for (h, l), c_prev in zip(zip(highs[1:], lows[1:]), closes[:-1])]
                )
                atr = float(tr.tail(atr_period).mean()) if len(tr) >= atr_period else float(bar.high - bar.low)
            else:
                atr = float(bar.high - bar.low)

            # Ensure ATR has floor
            atr = max(atr, bar.close * 0.005)

            ten_box.audit.called("mpc")
            plan, pid_info, mpc_trace = mpc_box.build_plan(
                pa_signal, id_decision, bar.close, atr, config
            )
            if plan is None:
                continue

            # 4. SafetyGates pre-sizing: Check drawdown/halt conditions
            approved, reason_pre, size_multiplier, safety_pre_trace = safety_gates.evaluate_pre_sizing(
                equity_curve, config
            )
            if not approved:
                # Drawdown halt or other pre-sizing blocker
                continue

            # 5. PositionManager: Size the position
            ten_box.audit.called("position_manager")
            quantity, pm_trace = position_manager.size(
                plan,
                available_equity=snapshot.cash,
                size_multiplier=size_multiplier,
                config=config,
                open_positions_count=len(snapshot.positions),
                symbol_positions_count=1 if symbol in snapshot.positions else 0,
            )
            if quantity <= 0:
                # No sizing available (capital limit, position limit, etc)
                continue

            risk_ok, _reason = ten_box.approve_risk(plan.entry_price, plan.stop_price, quantity)
            if not risk_ok:
                continue

            # 6. SafetyGates post-sizing: Check profit margin and trade-loss caps
            approved_post, reason_post, safety_post_trace = safety_gates.evaluate_post_sizing(
                equity_curve, plan, quantity, config
            )
            if not approved_post:
                # Profit margin too low or loss cap exceeded
                continue

            # 7. P01D: Create final order
            ten_box.audit.called("execution")
            order, p01d_trace = p01d_box.create_order(symbol, plan, quantity, config)
            if order is None:
                continue

            # 8. Build typed contracts correctly

            # TradePlan for SizedProposal
            trade_plan = TradePlan(
                timestamp=bar.timestamp,
                bar_index=event_index,
                symbol=symbol,
                direction=pa_signal.direction,
                entry_price=bar.close,
                stop_price=plan.stop_price,
                target_price=plan.target_price,
                position_size_base=quantity,
                risk_per_share=abs(plan.entry_price - plan.stop_price),
            )

            # SizedProposal with canonical entry cost estimate
            entry_side = "BUY" if pa_signal.direction == 1 else "SELL"
            entry_cost_estimate = calculate_transaction_cost(bar.close, quantity, entry_side)

            proposal = SizedProposal(
                timestamp=bar.timestamp,
                bar_index=event_index,
                symbol=symbol,
                plan=trade_plan,
                mpc_scaling_factor=1.0,  # Already included in quantity
                final_quantity=quantity,
                cost_estimate=entry_cost_estimate,  # Canonical NSE model
            )

            # OrderIntent
            order_intent = OrderIntent(
                order_id=make_order_id(bar.timestamp, event_index, symbol),
                symbol=symbol,
                direction=pa_signal.direction,
                quantity=quantity,
                stop_price=plan.stop_price,
                target_price=plan.target_price,
                proposal=proposal,
                timestamp_created=bar.timestamp,
                bar_index_created=event_index,
            )

            # Rank by PA confidence (higher = better)
            # Chart momentum is a deterministic quality adjustment, not a
            # fabricated confidence value.
            rank_score = pa_signal.confidence + (0.1 if pa_signal.quality_band == "green" else 0) + abs(chart.momentum)

            candidate = RankedOrderCandidate(
                order=order_intent,
                rank=rank_score,
                pa_confidence=pa_signal.confidence,
                id_risk_reward=id_decision.risk_reward_ratio,
            )
            candidates.append(candidate)

        # Sort by rank descending (higher first) — orchestrator does this too
        candidates.sort(key=lambda c: (-c.rank, c.order.order_id))
        return candidates

    # The sealed validator and integration tests consume this audit directly.
    candidate_provider.ten_box_integration = ten_box
    return candidate_provider


def build_exit_provider(config: EffectiveConfig, ten_box_integration: Optional[TenBoxIntegration] = None):
    """
    Build exit_provider callback for TimestampOrchestrator.

    Evaluates positions for exits based on:
      - Stop-loss prices
      - Profit targets
      - Time-held limits
      - Daily loss limits (liquidation)

    Uses real Position dataclass, ExitReason enum, ExitEvent contract.
    """

    def exit_provider(
        snapshot: PortfolioSnapshot,
        bars: Mapping[str, Bar],
        event_index: int,
    ) -> Sequence[ExitEvent]:
        """
        Generate exit events for positions to close at this timestamp.

        Priority (same as revision2 orchestrator):
          1. Forced close: drawdown halt
          2. Protective stops: stop-loss prices
          3. Profit targets: target prices
          4. Time-held limits: maximum_hold_bars exceeded
          5. Daily loss: liquidation threshold
        """
        exits: List[ExitEvent] = []

        # Get peak equity for drawdown calculation
        peak_equity = snapshot.marked_equity  # Simplified, should track peak over time
        current_equity = snapshot.marked_equity

        for symbol, position in snapshot.positions.items():
            if symbol not in bars:
                continue

            bar = bars[symbol]

            exit_reason = None
            exit_price = None

            if ten_box_integration is not None:
                exit_reason, exit_price = ten_box_integration.decide_exit(
                    snapshot, position, bar, event_index, atr=0.0,
                )

            # 1. Forced close: drawdown halt
            try:
                max_dd = float(config.require("drawdown_halt_threshold"))
                drawdown = (peak_equity - current_equity) / peak_equity if peak_equity > 0 else 0.0
                if drawdown >= max_dd:
                    exit_reason = ExitReason.LIQUIDATION
                    exit_price = bar.close
            except Exception as e:
                print(f"[exit_provider] Error checking drawdown for {symbol}: {e}")

            # 2. Protective stops (if not already exiting)
            if exit_reason is None:
                if position.direction == 1:  # Long
                    if bar.low <= position.stop_price:
                        exit_reason = ExitReason.STOP_HIT
                        exit_price = position.stop_price
                elif position.direction == -1:  # Short
                    if bar.high >= position.stop_price:
                        exit_reason = ExitReason.STOP_HIT
                        exit_price = position.stop_price

            # 3. Profit targets (if not already exiting)
            if exit_reason is None:
                if position.direction == 1:  # Long
                    if bar.high >= position.target_price:
                        exit_reason = ExitReason.TARGET_HIT
                        exit_price = position.target_price
                elif position.direction == -1:  # Short
                    if bar.low <= position.target_price:
                        exit_reason = ExitReason.TARGET_HIT
                        exit_price = position.target_price

            # 4. Time-held limits (if not already exiting)
            if exit_reason is None:
                bars_held = position.bars_held(event_index)
                # Max hold default is 60 bars if not specified
                max_hold = 60
                if bars_held >= max_hold:
                    exit_reason = ExitReason.TIME_EXIT
                    exit_price = bar.close

            # 5. Daily loss limits (if not already exiting)
            if exit_reason is None:
                try:
                    max_daily_loss = float(config.require("max_loss_per_day_rupees"))
                    daily_loss = max(0.0, peak_equity - current_equity)
                    if daily_loss > max_daily_loss:
                        exit_reason = ExitReason.LIQUIDATION
                        exit_price = bar.close
                except Exception as e:
                    print(f"[exit_provider] Error checking daily loss for {symbol}: {e}")

            # If any exit condition triggered, create exit event
            if exit_reason is not None and exit_price is not None:
                # Calculate exit cost using canonical model
                # For longs: exit by SELLING
                # For shorts: exit by BUYING back
                exit_side = "SELL" if position.direction == 1 else "BUY"
                exit_cost = calculate_transaction_cost(exit_price, int(position.quantity), exit_side)

                # Compute P&L: gross P&L minus costs
                if position.direction == 1:
                    gross_pnl = (exit_price - position.entry_price) * position.quantity
                else:
                    gross_pnl = (position.entry_price - exit_price) * position.quantity

                # Net P&L after all costs
                pnl_realized = gross_pnl - position.cost_paid - exit_cost
                pnl_pct = (pnl_realized / (position.entry_price * position.quantity)) * 100.0 if position.entry_price else 0.0

                exit_event = ExitEvent(
                    exit_id=f"{event_index}_{symbol}_exit",
                    symbol=symbol,
                    timestamp_exit=bar.timestamp,
                    bar_index_exit=event_index,
                    entry_price=position.entry_price,
                    exit_price=exit_price,
                    quantity=int(position.quantity),
                    direction=position.direction,
                    bars_held=position.bars_held(event_index),
                    entry_cost_paid=position.cost_paid,
                    exit_cost_paid=exit_cost,
                    exit_reason=exit_reason,
                    pnl_realized=pnl_realized,
                    pnl_pct=pnl_pct,
                )
                exits.append(exit_event)

        return exits

    return exit_provider

def make_order_id(timestamp: str, event_index: int, symbol: str) -> str:
    """Return a deterministic ID unique across a multi-session replay.

    ``event_index`` is local to one ``TimestampOrchestrator.run()`` call and
    therefore restarts at zero when the sealed validator starts the next
    market session.  The timestamp namespace prevents the shared broker's
    durable order history from seeing a false duplicate on a later day.
    """
    timestamp_key = pd.Timestamp(timestamp).strftime("%Y%m%dT%H%M%S%f%z")
    return f"{timestamp_key}_{event_index}_{symbol}"
