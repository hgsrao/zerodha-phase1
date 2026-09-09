#!/usr/bin/env python3
"""
Portfolio-level wrapper around per-symbol Revision2Orchestrator.

Runs multiple symbols in sequence, aggregates results into portfolio metrics
(net PnL, Sharpe ratio, max drawdown, total trades, etc.) for Ray Tune calibration.

Each symbol uses the same calibration_overrides and starting_equity.
Results are aggregated at portfolio level for optimization.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.orchestrator import Revision2Orchestrator


class PortfolioOrchestrator:
    """
    Wraps Revision2Orchestrator (per-symbol) into portfolio-level interface.

    Usage:
        orchestrator = PortfolioOrchestrator(
            symbols=["INFY", "RELIANCE", "HDFC"],
            registry=registry,
            calibration_overrides={...},
            starting_equity=100_000.0
        )
        result = orchestrator.run(symbol_bars)  # {symbol: DataFrame}

    Returns:
        Portfolio aggregated metrics:
        - net_pnl: sum across symbols
        - sharpe_ratio: portfolio-level Sharpe (based on daily returns)
        - max_drawdown_fraction: worst drawdown across equity curve
        - completed_trades: total trades across all symbols
        - symbols_traded: count of symbols with ≥1 trade
        - per_symbol_results: individual symbol results for inspection
    """

    def __init__(
        self,
        symbols: List[str],
        registry: Optional[CanonicalParameterRegistry] = None,
        calibration_overrides: Optional[Dict[str, Any]] = None,
        starting_equity: float = 100_000.0,
    ):
        """
        Initialize portfolio orchestrator.

        Args:
            symbols: List of symbol strings (e.g., ["INFY", "RELIANCE"])
            registry: CanonicalParameterRegistry (default: creates new)
            calibration_overrides: Parameter overrides for calibration
            starting_equity: Starting capital (shared across all symbols)
        """
        self.symbols = symbols
        self.registry = registry or CanonicalParameterRegistry()
        self.calibration_overrides = calibration_overrides or {}
        self.starting_equity = starting_equity

        # Per-symbol orchestrators (lazy initialized on run)
        self._orchestrators: Dict[str, Revision2Orchestrator] = {}

    def run(
        self,
        symbol_bars: Dict[str, pd.DataFrame],
        warmup: int = 60,
    ) -> Dict[str, Any]:
        """
        Run portfolio backtest across all symbols.

        Args:
            symbol_bars: {symbol: DataFrame} with OHLCV bars
            warmup: Warmup bars to skip

        Returns:
            Portfolio-aggregated metrics + per-symbol results
        """

        per_symbol_results = {}
        per_symbol_pnl = {}
        per_symbol_trades = {}
        per_symbol_equity_curves = {}
        symbols_with_trades = []

        # Run each symbol sequentially
        for symbol in self.symbols:
            if symbol not in symbol_bars:
                # Symbol not in data, skip
                per_symbol_results[symbol] = {"skipped": True, "reason": "data_not_found"}
                continue

            try:
                # Create per-symbol orchestrator with same overrides
                orch = Revision2Orchestrator(
                    symbol=symbol,
                    registry=self.registry,
                    calibration_overrides=self.calibration_overrides,
                    starting_equity=self.starting_equity,
                )
                self._orchestrators[symbol] = orch

                # Run on this symbol's data
                bars = symbol_bars[symbol]
                result = orch.run(bars, warmup=warmup)

                # Store results
                per_symbol_results[symbol] = result
                per_symbol_pnl[symbol] = result.get("net_pnl", 0.0)
                per_symbol_trades[symbol] = result.get("completed_trades", 0)

                # Track symbols with at least 1 trade
                if per_symbol_trades[symbol] > 0:
                    symbols_with_trades.append(symbol)

                # Build equity curve from this symbol's results
                # (simple: starting equity + cumulative PnL)
                equity_curve = [self.starting_equity]
                for trade in result.get("trades", []):
                    equity_curve.append(equity_curve[-1] + trade.get("pnl", 0.0))
                per_symbol_equity_curves[symbol] = equity_curve

            except Exception as e:
                per_symbol_results[symbol] = {
                    "skipped": True,
                    "reason": f"orchestrator_error: {str(e)}",
                }
                continue

        # Aggregate portfolio metrics
        total_pnl = sum(per_symbol_pnl.values())
        total_trades = sum(per_symbol_trades.values())
        symbols_traded_count = len(symbols_with_trades)

        # Portfolio equity curve: start with equity, add all symbol PnLs
        # Treat each symbol completion as a portfolio-level equity update
        portfolio_equity_curve = [self.starting_equity * len(self.symbols)]
        for symbol in self.symbols:
            if symbol in per_symbol_equity_curves:
                # Add this symbol's PnL progression to portfolio
                for pnl in per_symbol_equity_curves[symbol][1:]:
                    portfolio_equity_curve.append(portfolio_equity_curve[-1] + pnl)

        # Calculate Sharpe ratio from portfolio returns
        # Assume daily returns (simplified: use equity curve changes)
        portfolio_returns = np.diff(portfolio_equity_curve) / portfolio_equity_curve[:-1]
        if len(portfolio_returns) > 1:
            mean_return = np.mean(portfolio_returns)
            std_return = np.std(portfolio_returns)
            sharpe_ratio = (mean_return / std_return * np.sqrt(252)) if std_return > 0 else 0.0
        else:
            sharpe_ratio = 0.0

        # Calculate max drawdown from portfolio equity curve
        max_dd = self._calculate_max_drawdown(portfolio_equity_curve)

        return {
            # Portfolio-level aggregates (what Ray Tune optimizes)
            "net_pnl": total_pnl,
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown_fraction": max_dd,
            "completed_trades": total_trades,
            "symbols_traded": symbols_traded_count,

            # Portfolio metrics
            "symbols_requested": len(self.symbols),
            "symbols_processed": len([s for s in self.symbols if s in per_symbol_results]),
            "symbols_skipped": len(self.symbols) - len(per_symbol_results),

            # Per-symbol breakdown (for inspection)
            "per_symbol_results": per_symbol_results,
            "per_symbol_pnl": per_symbol_pnl,
            "per_symbol_trades": per_symbol_trades,
            "symbols_with_trades": symbols_with_trades,

            # Equity tracking
            "portfolio_starting_equity": self.starting_equity * len(self.symbols),
            "portfolio_ending_equity": self.starting_equity * len(self.symbols) + total_pnl,
            "portfolio_equity_curve": portfolio_equity_curve,
        }

    @staticmethod
    def _calculate_max_drawdown(equity_curve: List[float]) -> float:
        """Calculate maximum drawdown as fraction of peak."""
        if not equity_curve or len(equity_curve) < 2:
            return 0.0

        equity_array = np.array(equity_curve)
        peak = np.maximum.accumulate(equity_array)
        drawdown = (peak - equity_array) / peak
        return float(np.max(drawdown)) if len(drawdown) > 0 else 0.0
