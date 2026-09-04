#!/usr/bin/env python3
"""Portfolio manager - proper cash/equity accounting"""
from dataclasses import dataclass
from typing import Dict

@dataclass
class Position:
    symbol: str
    qty: int
    entry_price: float
    entry_time: str
    stop_loss: float
    profit_target: float

class PortfolioManager:
    def __init__(self, initial_capital):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: Dict[str, Position] = {}
        self.closed_trades = []
        self.equity_curve = [initial_capital]

    def enter(self, symbol, qty, entry_price, costs, entry_time, stop_loss, profit_target):
        """Enter position with costs"""
        total_cost = qty * entry_price + costs
        if total_cost > self.cash:
            raise ValueError(f"Insufficient cash: need {total_cost}, have {self.cash}")

        self.cash -= total_cost
        self.positions[symbol] = Position(
            symbol=symbol, qty=qty, entry_price=entry_price,
            entry_time=entry_time, stop_loss=stop_loss, profit_target=profit_target
        )

    def exit(self, symbol, exit_price, costs, exit_time, exit_reason):
        """Exit position with costs"""
        if symbol not in self.positions:
            raise ValueError(f"No position in {symbol}")

        pos = self.positions[symbol]
        proceeds = pos.qty * exit_price - costs
        realized_pnl = proceeds - (pos.qty * pos.entry_price)

        self.cash += proceeds
        self.closed_trades.append({
            'symbol': symbol,
            'entry_price': pos.entry_price,
            'exit_price': exit_price,
            'qty': pos.qty,
            'entry_time': pos.entry_time,
            'exit_time': exit_time,
            'exit_reason': exit_reason,
            'realized_pnl': realized_pnl,
            'costs': costs
        })

        del self.positions[symbol]
        return realized_pnl

    def get_unrealized_pnl(self, last_prices: Dict[str, float]):
        """Calculate unrealized P&L"""
        total = 0
        for symbol, pos in self.positions.items():
            if symbol in last_prices:
                total += (last_prices[symbol] - pos.entry_price) * pos.qty
        return total

    def get_equity(self, last_prices: Dict[str, float]):
        """Total equity = cash + position values + unrealized"""
        position_value = sum(
            last_prices.get(s, p.entry_price) * p.qty
            for s, p in self.positions.items()
        )
        return self.cash + position_value

    def get_total_pnl(self):
        """Total P&L from all closed trades"""
        return sum(t['realized_pnl'] for t in self.closed_trades)
