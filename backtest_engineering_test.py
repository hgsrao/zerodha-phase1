#!/usr/bin/env python3
"""Engineering test: Deliberately qualifying signal (confidence 0.60)
exercises full trade lifecycle with equity reconciliation and gate telemetry"""

import pandas as pd
import json
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional
from data_loader_frozen import FrozenDataLoader
from portfolio_manager_correct import PortfolioManager
from gates_framework import EntryDecisionEngine, SafetyGateConfig, GateLogger, SystemState, EntrySignal
from zerodha_intraday_costs import buy_cost, sell_cost

@dataclass
class GateDecisionRecord:
    """Track every gate decision for telemetry"""
    timestamp: str
    symbol: str
    gates_evaluated: Dict[str, bool]  # {gate_name: pass/fail}
    entry_allowed: bool
    adjusted_quantity: int
    rejection_reason: Optional[str]
    confidence: float
    rrr: float

@dataclass
class EquityCheckpoint:
    """Verify equity formula at each checkpoint"""
    timestamp: str
    bar_index: int
    cash: float
    position_value: float
    unrealized_pnl: float
    reported_equity: float
    calculated_equity: float
    matches: bool
    error: Optional[str]

class EngineeringTest:
    """Step 1: Engineering test with deliberate qualifying signal"""

    def __init__(self, initial_capital=1000000):
        self.initial_capital = initial_capital
        self.portfolio = PortfolioManager(initial_capital)
        self.loader = FrozenDataLoader()
        self.safety_config = SafetyGateConfig()
        self.gate_logger = GateLogger()
        self.entry_engine = EntryDecisionEngine(self.safety_config, self.gate_logger)

        self.gate_decisions = []
        self.equity_checkpoints = []
        self.errors = []

    def deliberate_signal(self, current_bar, prev_bar, symbol):
        """Engineering signal: close > open (guaranteed entries)
        Confidence: 0.60 (above 0.55 gate minimum)
        This ensures we exercise the full trade lifecycle"""
        return current_bar['close'] > current_bar['open']

    def calculate_equity(self, last_prices: Dict[str, float]) -> tuple:
        """Calculate equity from first principles and verify formula"""
        cash = self.portfolio.cash
        position_value = sum(
            last_prices.get(s, p.entry_price) * p.qty
            for s, p in self.portfolio.positions.items()
        )
        unrealized_pnl = self.portfolio.get_unrealized_pnl(last_prices)
        calculated_equity = cash + position_value

        return cash, position_value, unrealized_pnl, calculated_equity

    def verify_equity(self, timestamp, bar_idx, last_prices):
        """Checkpoint: Verify equity formula is correct"""
        try:
            cash, pos_value, unreal_pnl, calc_equity = self.calculate_equity(last_prices)
            reported_equity = self.portfolio.get_equity(last_prices)

            checkpoint = EquityCheckpoint(
                timestamp=str(timestamp),
                bar_index=bar_idx,
                cash=cash,
                position_value=pos_value,
                unrealized_pnl=unreal_pnl,
                reported_equity=reported_equity,
                calculated_equity=calc_equity,
                matches=(abs(reported_equity - calc_equity) < 0.01),
                error=None
            )

            if not checkpoint.matches:
                checkpoint.error = f"Mismatch: reported {reported_equity:.2f} vs calculated {calc_equity:.2f}"
                self.errors.append(checkpoint.error)

            self.equity_checkpoints.append(checkpoint)
            return checkpoint
        except Exception as e:
            error_msg = f"Equity check failed at bar {bar_idx}: {str(e)}"
            self.errors.append(error_msg)
            raise RuntimeError(error_msg)

    def record_gate_decision(self, timestamp, symbol, signal_qual, can_enter, size, reason, state):
        """Record gate decision with full telemetry"""
        try:
            decision = GateDecisionRecord(
                timestamp=str(timestamp),
                symbol=symbol,
                gates_evaluated={},  # Would populate from gate_logger if available
                entry_allowed=can_enter,
                adjusted_quantity=size,
                rejection_reason=reason if not can_enter else None,
                confidence=signal_qual.get('confidence', 0.60),
                rrr=signal_qual.get('rrr', 1.5)
            )
            self.gate_decisions.append(decision)
        except Exception as e:
            error_msg = f"Gate decision recording failed for {symbol}: {str(e)}"
            self.errors.append(error_msg)
            raise RuntimeError(error_msg)

    def run(self, symbols, start_date="2023-08-14", end_date="2026-08-14"):
        """Run engineering test with deliberately qualifying signal"""

        print(f"\n{'='*90}")
        print(f"ENGINEERING TEST: 5-symbol deliberate signal")
        print(f"Signal: close > open (confidence 0.60, guaranteed entries)")
        print(f"Purpose: Exercise full trade lifecycle with equity reconciliation")
        print(f"{'='*90}\n")

        # Load data
        print("Loading frozen data...")
        data = self.loader.load_multiple(symbols)
        print(f"[OK] Loaded {len(data)} symbols\n")

        # Filter to date range
        for symbol in data:
            df = data[symbol]
            df = df[(df['timestamp'] >= start_date) & (df['timestamp'] <= end_date)]
            data[symbol] = df

        # Find minimum length
        min_len = min(len(df) for df in data.values())
        print(f"Processing {min_len} bars per symbol\n")

        last_prices = {s: data[s].iloc[0]['close'] for s in symbols}
        equity_curve = [self.initial_capital]

        # Bar-by-bar iteration
        for bar_idx in range(1, min_len - 1):
            try:
                # Update prices
                for symbol in symbols:
                    last_prices[symbol] = data[symbol].iloc[bar_idx]['close']

                # CHECKPOINT 1: Verify equity before any entry/exit
                self.verify_equity(
                    data[symbols[0]].iloc[bar_idx]['timestamp'],
                    bar_idx,
                    last_prices
                )

                # Check exits
                for symbol in list(self.portfolio.positions.keys()):
                    try:
                        pos = self.portfolio.positions[symbol]
                        bar = data[symbol].iloc[bar_idx]

                        should_exit = False
                        reason = ""
                        exit_price = None

                        if bar['low'] <= pos.stop_loss:
                            should_exit = True
                            reason = "stop_loss"
                            exit_price = pos.stop_loss
                        elif bar['high'] >= pos.profit_target:
                            should_exit = True
                            reason = "profit_target"
                            exit_price = pos.profit_target

                        if should_exit:
                            # Use real cost calculator
                            exit_value = pos.qty * exit_price
                            cost_breakdown = sell_cost(exit_value)
                            total_costs = cost_breakdown.total

                            self.portfolio.exit(
                                symbol, exit_price, total_costs,
                                str(bar['timestamp']),
                                reason
                            )

                            print(f"  EXIT: {symbol} @ Rs{exit_price:.2f} ({reason})")

                    except Exception as e:
                        error_msg = f"Exit processing failed for {symbol} at bar {bar_idx}: {str(e)}"
                        self.errors.append(error_msg)
                        raise RuntimeError(error_msg)

                # CHECKPOINT 2: Verify equity after exits
                self.verify_equity(
                    data[symbols[0]].iloc[bar_idx]['timestamp'],
                    bar_idx,
                    last_prices
                )

                # Check entries
                for symbol in symbols:
                    try:
                        if symbol in self.portfolio.positions:
                            continue

                        current_bar = data[symbol].iloc[bar_idx]
                        prev_bar = data[symbol].iloc[bar_idx - 1]
                        next_bar = data[symbol].iloc[bar_idx + 1]

                        # DELIBERATE SIGNAL: close > open (guaranteed to trigger)
                        if self.deliberate_signal(current_bar, prev_bar, symbol):
                            entry_price = next_bar['open']

                            # Create signal with high confidence (0.60 > gate 0.55)
                            signal = EntrySignal(
                                symbol=symbol,
                                entry_price=entry_price,
                                stop_loss_price=entry_price * 0.97,
                                profit_target_price=entry_price * 1.03,
                                confidence=0.60,  # Above 0.55 gate minimum
                                suggested_quantity=100,
                                position_notional=100 * entry_price,
                                risk_reward_ratio=1.5
                            )

                            # Create state
                            current_equity = self.portfolio.get_equity(last_prices)
                            state = SystemState(
                                portfolio_value=current_equity,
                                current_dd_percent=self._get_dd(equity_curve),
                                current_lambda=len(self.portfolio.positions) / 5.0,
                                daily_realized_loss=0,
                                daily_unrealized_loss=0,
                                open_positions_count=len(self.portfolio.positions),
                                open_positions=[],
                                market_data_age_seconds=0,
                                broker_connected=True,
                                broker_offline_seconds=0,
                                kill_switch_active=False,
                                circuit_breaker_triggered=False
                            )

                            # Gate decision
                            can_enter, size, reason = self.entry_engine.can_enter(signal, state)

                            # Record decision
                            self.record_gate_decision(
                                current_bar['timestamp'],
                                symbol,
                                {'confidence': 0.60, 'rrr': 1.5},
                                can_enter,
                                size,
                                reason,
                                state
                            )

                            if can_enter:
                                # Use real cost calculator
                                entry_value = size * entry_price
                                cost_breakdown = buy_cost(entry_value)
                                total_costs = cost_breakdown.total

                                self.portfolio.enter(
                                    symbol, size, entry_price, total_costs,
                                    str(next_bar['timestamp']),
                                    entry_price * 0.97,
                                    entry_price * 1.03
                                )

                                print(f"ENTRY:  {symbol} @ Rs{entry_price:.2f} (confidence 0.60)")

                    except Exception as e:
                        error_msg = f"Entry processing failed for {symbol} at bar {bar_idx}: {str(e)}"
                        self.errors.append(error_msg)
                        raise RuntimeError(error_msg)

                # CHECKPOINT 3: Verify equity after entries
                current_equity = self.portfolio.get_equity(last_prices)
                equity_curve.append(current_equity)
                self.verify_equity(
                    data[symbols[0]].iloc[bar_idx]['timestamp'],
                    bar_idx,
                    last_prices
                )

                if bar_idx % 2000 == 0:
                    print(f"Bar {bar_idx}: Equity Rs{current_equity:,.0f}, Positions: {len(self.portfolio.positions)}")

            except Exception as e:
                error_msg = f"Bar processing failed at index {bar_idx}: {str(e)}"
                self.errors.append(error_msg)
                print(f"[FAIL] ERROR: {error_msg}")
                raise RuntimeError(error_msg)

        # Force-close all remaining positions at final price
        print("\nForce-closing remaining positions at market close...")
        for symbol in list(self.portfolio.positions.keys()):
            try:
                pos = self.portfolio.positions[symbol]
                final_price = data[symbol].iloc[-1]['close']
                exit_value = pos.qty * final_price
                cost_breakdown = sell_cost(exit_value)
                total_costs = cost_breakdown.total

                self.portfolio.exit(
                    symbol, final_price, total_costs,
                    str(data[symbol].iloc[-1]['timestamp']),
                    "end_of_sample"
                )
                print(f"  CLOSE: {symbol} @ Rs{final_price:.2f}")
            except Exception as e:
                error_msg = f"Force-close failed for {symbol}: {str(e)}"
                self.errors.append(error_msg)
                raise RuntimeError(error_msg)

        # Final results
        final_equity = self.portfolio.get_equity(last_prices)
        total_pnl = final_equity - self.initial_capital
        total_trades = len(self.portfolio.closed_trades)
        win_rate = sum(1 for t in self.portfolio.closed_trades if t['realized_pnl'] > 0) / max(total_trades, 1)

        print(f"\n{'='*90}")
        print("ENGINEERING TEST RESULTS")
        print(f"{'='*90}")
        print(f"Initial Capital:      Rs{self.initial_capital:>15,.0f}")
        print(f"Final Equity:         Rs{final_equity:>15,.0f}")
        print(f"Total P&L:            Rs{total_pnl:>15,.0f}")
        print(f"Total Trades:         {total_trades:>15}")
        print(f"Winning Trades:       {sum(1 for t in self.portfolio.closed_trades if t['realized_pnl'] > 0):>15}")
        print(f"Losing Trades:        {sum(1 for t in self.portfolio.closed_trades if t['realized_pnl'] <= 0):>15}")
        print(f"Win Rate:             {win_rate*100:>14.1f}%")
        print(f"Max Drawdown:         {self._get_max_dd(equity_curve):>14.2f}%")
        print(f"\nEquity Checkpoints:   {len(self.equity_checkpoints):>15}")
        print(f"Checkpoints Passed:   {sum(1 for c in self.equity_checkpoints if c.matches):>15}")
        print(f"Gate Decisions:       {len(self.gate_decisions):>15}")
        print(f"Errors Encountered:   {len(self.errors):>15}")
        print(f"{'='*90}\n")

        if self.errors:
            print("[FAIL] ERRORS DETECTED:")
            for i, error in enumerate(self.errors, 1):
                print(f"  {i}. {error}")
            print()

        return {
            'initial_capital': self.initial_capital,
            'final_equity': final_equity,
            'total_pnl': total_pnl,
            'return_percent': (total_pnl / self.initial_capital) * 100,
            'total_trades': total_trades,
            'winning_trades': sum(1 for t in self.portfolio.closed_trades if t['realized_pnl'] > 0),
            'losing_trades': sum(1 for t in self.portfolio.closed_trades if t['realized_pnl'] <= 0),
            'win_rate': win_rate,
            'max_drawdown': self._get_max_dd(equity_curve),
            'equity_curve': equity_curve,
            'trades': self.portfolio.closed_trades,
            'gate_decisions': [asdict(g) for g in self.gate_decisions],
            'equity_checkpoints': [asdict(c) for c in self.equity_checkpoints],
            'errors': self.errors,
            'test_type': 'ENGINEERING (deliberate signal, full lifecycle)',
            'signal_confidence': 0.60,
            'status': 'PASS' if len(self.errors) == 0 else 'FAIL'
        }

    def _get_dd(self, equity_curve):
        """Current drawdown %"""
        if not equity_curve:
            return 0
        peak = max(equity_curve)
        current = equity_curve[-1]
        return ((peak - current) / peak * 100) if peak > 0 else 0

    def _get_max_dd(self, equity_curve):
        """Max drawdown %"""
        if not equity_curve:
            return 0
        peak = equity_curve[0]
        max_dd = 0
        for val in equity_curve:
            if val > peak:
                peak = val
            dd = (peak - val) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
        return max_dd

if __name__ == "__main__":
    test = EngineeringTest(initial_capital=1000000)
    symbols_5 = ['INFY', 'TCS', 'RELIANCE', 'SUNPHARMA', 'HDFCLIFE']

    results = test.run(symbols_5)

    # Save results
    with open("ENGINEERING_TEST_RESULTS.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    print(f"[OK] Results saved to ENGINEERING_TEST_RESULTS.json")
    print(f"\nTest Status: {results['status']}")
    if results['status'] == 'FAIL':
        print(f"[WARN]  {len(results['errors'])} errors found - review above")
    else:
        print(f"[OK] No errors encountered - ready for Step 2 (gate tests)")
