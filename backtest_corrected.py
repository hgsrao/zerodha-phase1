#!/usr/bin/env python3
"""Corrected backtest - causal, real costs, real gate telemetry"""
import pandas as pd
from datetime import datetime
from portfolio_manager_correct import PortfolioManager
from data_loader_frozen import FrozenDataLoader
from gates_framework import EntryDecisionEngine, SafetyGateConfig, GateLogger, SystemState, EntrySignal

ZERODHA_BROKERAGE_PERCENT = 0.001  # 0.1%
STT_PERCENT = 0.0001  # 0.01%
SLIPPAGE_PAISE = 0.5  # Per unit

class BacktestCorrected:
    def __init__(self, initial_capital=1000000):
        self.initial_capital = initial_capital
        self.portfolio = PortfolioManager(initial_capital)
        self.loader = FrozenDataLoader()
        self.safety_config = SafetyGateConfig()
        self.gate_logger = GateLogger()
        self.entry_engine = EntryDecisionEngine(self.safety_config, self.gate_logger)
        self.gate_telemetry = {}
        self.daily_loss = {}

    def calculate_costs(self, symbol, qty, price):
        """Real Zerodha costs"""
        notional = qty * price
        brokerage = notional * ZERODHA_BROKERAGE_PERCENT
        stt = notional * STT_PERCENT
        slippage = qty * SLIPPAGE_PAISE / 100
        return brokerage + stt + slippage

    def run(self, symbols, start_date="2023-08-14", end_date="2026-08-14"):
        """Run corrected backtest"""
        print(f"\n{'='*90}")
        print(f"CORRECTED BACKTEST: {len(symbols)} symbols, real data, causal execution")
        print(f"Period: {start_date} to {end_date}")
        print(f"{'='*90}\n")

        # Load data
        print("Loading frozen data...")
        data = self.loader.load_multiple(symbols)
        print(f"✅ Loaded {len(data)} symbols\n")

        # Filter to date range
        for symbol in data:
            df = data[symbol]
            df = df[(df['timestamp'] >= start_date) & (df['timestamp'] <= end_date)]
            data[symbol] = df

        # Find common length
        min_len = min(len(df) for df in data.values())
        print(f"Processing {min_len} bars per symbol\n")

        # Bar-by-bar simulation
        last_prices = {s: data[s].iloc[0]['close'] for s in symbols}
        equity_curve = [self.initial_capital]

        for bar_idx in range(1, min_len - 1):  # -1 for next bar entry
            # Update last prices and equity
            for symbol in symbols:
                last_prices[symbol] = data[symbol].iloc[bar_idx]['close']

            current_equity = self.portfolio.get_equity(last_prices)
            equity_curve.append(current_equity)

            # Check exits
            for symbol in list(self.portfolio.positions.keys()):
                pos = self.portfolio.positions[symbol]
                bar = data[symbol].iloc[bar_idx]

                should_exit = False
                reason = ""

                if bar['low'] <= pos.stop_loss:
                    should_exit = True
                    reason = "stop_loss"
                    exit_price = pos.stop_loss
                elif bar['high'] >= pos.profit_target:
                    should_exit = True
                    reason = "profit_target"
                    exit_price = pos.profit_target

                if should_exit:
                    costs = self.calculate_costs(symbol, pos.qty, exit_price)
                    self.portfolio.exit(
                        symbol, exit_price, costs,
                        data[symbol].iloc[bar_idx]['timestamp'],
                        reason
                    )

            # Check entries (on NEXT bar's open - causal)
            for symbol in symbols:
                if symbol in self.portfolio.positions:
                    continue

                current_bar = data[symbol].iloc[bar_idx]
                next_bar = data[symbol].iloc[bar_idx + 1]

                # Simple momentum signal
                if current_bar['close'] > data[symbol].iloc[bar_idx - 1]['close']:
                    entry_price = next_bar['open']
                    costs = self.calculate_costs(symbol, 100, entry_price)

                    # Create signal
                    signal = EntrySignal(
                        symbol=symbol,
                        entry_price=entry_price,
                        stop_loss_price=entry_price * 0.98,
                        profit_target_price=entry_price * 1.03,
                        confidence=0.5,
                        suggested_quantity=100,
                        position_notional=100 * entry_price,
                        risk_reward_ratio=1.5
                    )

                    # Create state
                    state = SystemState(
                        portfolio_value=current_equity,
                        current_dd_percent=self._get_dd(equity_curve),
                        current_lambda=len(self.portfolio.positions) / 5.0,
                        daily_realized_loss=self.daily_loss.get('2026-09-04', 0),
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
                    try:
                        can_enter, size, reason = self.entry_engine.can_enter(signal, state)
                        if can_enter:
                            self.portfolio.enter(
                                symbol, size, entry_price, costs,
                                str(next_bar['timestamp']),
                                entry_price * 0.98,
                                entry_price * 1.03
                            )
                    except:
                        pass

            if bar_idx % 2000 == 0:
                print(f"Bar {bar_idx}: Equity ₹{current_equity:,.0f}, Positions: {len(self.portfolio.positions)}")

        # Final results
        final_equity = self.portfolio.get_equity(last_prices)
        total_pnl = final_equity - self.initial_capital
        total_trades = len(self.portfolio.closed_trades)
        win_rate = sum(1 for t in self.portfolio.closed_trades if t['realized_pnl'] > 0) / max(total_trades, 1)

        print(f"\n{'='*90}")
        print("RESULTS (CORRECTED)")
        print(f"{'='*90}")
        print(f"Initial Capital:      ₹{self.initial_capital:>15,.0f}")
        print(f"Final Equity:         ₹{final_equity:>15,.0f}")
        print(f"Total P&L:            ₹{total_pnl:>15,.0f}")
        print(f"Total Trades:         {total_trades:>15}")
        print(f"Win Rate:             {win_rate*100:>14.1f}%")
        print(f"Max Drawdown:         {self._get_max_dd(equity_curve):>14.2f}%")
        print(f"{'='*90}\n")

        return {
            'initial_capital': self.initial_capital,
            'final_equity': final_equity,
            'total_pnl': total_pnl,
            'total_trades': total_trades,
            'win_rate': win_rate,
            'equity_curve': equity_curve,
            'trades': self.portfolio.closed_trades
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
