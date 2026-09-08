"""
Integration of real Revision 2 boxes into timestamp orchestrator.
Adapters create callbacks that the orchestrator invokes at each timestamp.
"""

import pandas as pd
from typing import Mapping, Sequence, Optional, Dict
from revision4.contracts import Bar, EffectiveConfig, PortfolioSnapshot, ExitEvent
from revision4.timestamp_orchestrator import RankedOrderCandidate
from revision2.boxes import (
    PredictiveAnalyticsBox,
    IntelligentDiscriminationBox,
    ModelPredictiveControlBox,
    SafetyGatesTargetBox,
    PositionManagerBox,
    P01DBox,
)
from revision2.contracts import MarketSnapshot


def build_candidate_provider(config: EffectiveConfig):
    """
    Build candidate_provider callback for TimestampOrchestrator.

    Instantiates real Revision 2 boxes and runs full PA → ID → MPC → Safety → PM → P01D pipeline.

    This will be called at each timestamp with:
      - snapshot: frozen portfolio state
      - bars: {symbol: Bar} for current timestamp
      - event_index: chronological bar index

    Returns sequence of RankedOrderCandidate sorted by rank (higher first).

    Maintains bar history across calls so PA can calibrate properly.
    """
    # Instantiate boxes once (state shared across bars)
    pa_box = PredictiveAnalyticsBox()
    id_box = IntelligentDiscriminationBox()
    mpc_box = ModelPredictiveControlBox()
    safety_gates = SafetyGatesTargetBox()
    position_manager = PositionManagerBox()
    p01d_box = P01DBox()

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
          1. PA box generates signals (uses full bar history)
          2. ID box validates entry criteria
          3. MPC box creates trade plans with stops/targets
          4. SafetyGates pre-sizing checks (drawdown, etc)
          5. PositionManager sizes positions
          6. SafetyGates post-sizing checks (profit margin)
          7. P01D creates final orders
          8. Return ranked candidates
        """
        candidates: list[RankedOrderCandidate] = []

        # Build equity curve from snapshot
        equity_curve = [snapshot.marked_equity] if snapshot.marked_equity > 0 else [snapshot.starting_cash]

        # Process each symbol
        for symbol, bar in bars.items():
            try:
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

                # Calibrate PA on first call for this symbol
                if symbol not in pa_box._scale:
                    warmup_bars = df.iloc[:min(60, len(df))]  # Use first 60 bars as warmup
                    pa_box.calibrate(symbol, warmup_bars)

                # Build MarketSnapshot with full history
                market_snapshot = MarketSnapshot(
                    symbol=symbol,
                    timestamp=bar.timestamp,
                    bars=df,  # Full history available for PA
                    next_bar_open=None,
                )

                # 1. PA: Generate signal
                pa_signal, pa_trace = pa_box.evaluate(market_snapshot, config)
                if pa_signal.direction == 0:
                    # No directional bias
                    continue

                # 2. ID: Validate signal
                id_decision, id_trace = id_box.evaluate(pa_signal, config)
                if not id_decision.approved:
                    # Signal rejected
                    continue

                # 3. MPC: Build trade plan
                # Compute ATR from bar history
                closes = df['close'].to_numpy()
                highs = df['high'].to_numpy()
                lows = df['low'].to_numpy()
                atr_period = min(14, len(df))  # Standard ATR period
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

                # 6. SafetyGates post-sizing: Check profit margin and trade-loss caps
                approved_post, reason_post, safety_post_trace = safety_gates.evaluate_post_sizing(
                    equity_curve, plan, quantity, config
                )
                if not approved_post:
                    # Profit margin too low or loss cap exceeded
                    continue

                # 7. P01D: Create final order
                order, p01d_trace = p01d_box.create_order(symbol, plan, quantity, config)
                if order is None:
                    continue

                # 8. Build RankedOrderCandidate (now using actual orchestrator type)
                # Rank by PA confidence (higher = better)
                rank_score = pa_signal.confidence + (0.1 if pa_signal.quality_band == "green" else 0)

                from revision4.contracts import OrderIntent, SizedProposal, TradePlan as R4TradePlan

                # Build TradePlan for SizedProposal
                trade_plan = R4TradePlan(
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

                # Build SizedProposal
                proposal = SizedProposal(
                    timestamp=bar.timestamp,
                    bar_index=event_index,
                    symbol=symbol,
                    plan=trade_plan,
                    mpc_scaling_factor=1.0,  # Already included in quantity
                    final_quantity=quantity,
                    cost_estimate=quantity * bar.close * 0.001,  # Rough estimate
                )

                order_intent = OrderIntent(
                    order_id=f"{event_index}_{symbol}",
                    symbol=symbol,
                    direction=pa_signal.direction,
                    quantity=quantity,
                    stop_price=plan.stop_price,
                    target_price=plan.target_price,
                    proposal=proposal,
                    timestamp_created=bar.timestamp,
                    bar_index_created=event_index,
                )

                candidate = RankedOrderCandidate(
                    order=order_intent,
                    rank=rank_score,
                )
                candidates.append(candidate)

            except Exception as e:
                # Log but don't crash on one symbol's failure
                import traceback
                print(f"[candidate_provider] Error processing {symbol} at index {event_index}: {e}")
                traceback.print_exc()
                continue

        # Sort by rank descending (higher first) — orchestrator does this too
        candidates.sort(key=lambda c: (-c.rank, c.order.order_id))
        return candidates

    return candidate_provider


def build_exit_provider(config: EffectiveConfig):
    """
    Build exit_provider callback for TimestampOrchestrator.

    Evaluates positions for exits based on:
      - Stop-loss prices
      - Profit targets
      - Time-held limits
      - Daily loss limits (liquidation)
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
        exits: list[ExitEvent] = []

        for symbol, position in snapshot.positions.items():
            if symbol not in bars:
                continue

            bar = bars[symbol]

            exit_reason = None
            exit_price = None

            # Get peak equity for drawdown calculation
            peak_equity = snapshot.starting_cash  # Simplified: use starting capital as peak
            current_equity = snapshot.marked_equity

            # 1. Forced close: drawdown halt
            try:
                max_dd = float(config.require("drawdown_halt_threshold"))
                drawdown = (peak_equity - current_equity) / peak_equity if peak_equity > 0 else 0.0
                if drawdown >= max_dd:
                    exit_reason = "forced_close_drawdown_halt"
                    exit_price = bar.close
            except Exception:
                pass

            # 2. Protective stops (if not already exiting)
            if exit_reason is None:
                if position.get("side") == "BUY":
                    if bar.low <= position.get("stop_price", 0):
                        exit_reason = "stop_hit"
                        exit_price = position["stop_price"]
                else:  # SHORT
                    if bar.high >= position.get("stop_price", float('inf')):
                        exit_reason = "stop_hit"
                        exit_price = position["stop_price"]

            # 3. Profit targets (if not already exiting)
            if exit_reason is None:
                if position.get("side") == "BUY":
                    if bar.high >= position.get("target_price", float('inf')):
                        exit_reason = "target_hit"
                        exit_price = position["target_price"]
                else:  # SHORT
                    if bar.low <= position.get("target_price", 0):
                        exit_reason = "target_hit"
                        exit_price = position["target_price"]

            # 4. Time-held limits (if not already exiting)
            if exit_reason is None:
                bars_held = event_index - position.get("entry_bar_index", 0)
                max_hold = position.get("maximum_hold_bars", 60)
                if bars_held >= max_hold:
                    exit_reason = "maximum_hold_exceeded"
                    exit_price = bar.close

            # 5. Daily loss limits (if not already exiting)
            if exit_reason is None:
                try:
                    max_daily_loss = float(config.require("max_loss_per_day_rupees"))
                    daily_loss = max(0.0, peak_equity - current_equity)
                    if daily_loss > max_daily_loss:
                        exit_reason = "daily_loss_limit"
                        exit_price = bar.close
                except Exception:
                    pass

            # If any exit condition triggered, create exit event
            if exit_reason is not None and exit_price is not None:
                exit_event = ExitEvent(
                    exit_id=f"{event_index}_{symbol}_exit",
                    symbol=symbol,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    entry_cost_paid=position.get("entry_cost", 0.0),
                )
                exits.append(exit_event)

        return exits

    return exit_provider
