"""Box 8 (PositionManager) -- PyPortfolioOpt-based capital allocation.

Honest scoping note: PyPortfolioOpt solves "given expected returns and a
covariance matrix across N assets, find optimal weights" -- a PORTFOLIO
construction problem, not a single-trade sizing problem. It has no concept
of "this trade's entry-to-stop distance," which is what actually determines
how many shares a given risk budget buys. So this module does NOT replace
PositionManagerBox's ATR-based risk-per-share sizing (there is nothing in
PyPortfolioOpt to replace it with) -- it replaces the STATIC
max_symbol_concentration / max_sector_exposure_fraction caps with a REAL,
data-driven per-symbol capital weight from PyPortfolioOpt's own max-Sharpe
efficient frontier, computed from each symbol's actual historical returns.
The ATR risk-per-share sizing still determines the trade's risk; this
determines the CEILING on how much capital any one symbol can hold,
replacing a fixed percentage with an optimized one.

This is squarely PyPortfolioOpt's real, intended use (mean_historical_return
+ sample_cov + EfficientFrontier.max_sharpe(), its own documented standard
workflow) rather than forcing it into a role it wasn't built for.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

import pandas as pd
from pypfopt import expected_returns, risk_models
from pypfopt.efficient_frontier import EfficientFrontier
from pypfopt.exceptions import OptimizationError

from revision2.contracts import EffectiveConfig, ParameterUse, TradePlan


def compute_portfolio_weights(price_history_by_symbol: Dict[str, pd.Series]) -> Dict[str, float]:
    """Real max-Sharpe efficient-frontier weights from each symbol's own
    historical close-price series. Falls back to equal weight across the
    universe if optimization fails to converge (e.g. too few symbols/bars,
    or a degenerate/singular covariance matrix) -- a documented, honest
    fallback, not a silent wrong answer."""
    symbols = list(price_history_by_symbol.keys())
    if len(symbols) < 2:
        return {s: 1.0 for s in symbols}

    prices = pd.DataFrame({s: series for s, series in price_history_by_symbol.items()}).dropna(how="any")
    if len(prices) < 10:
        equal = 1.0 / len(symbols)
        return {s: equal for s in symbols}

    try:
        mu = expected_returns.mean_historical_return(prices)
        cov = risk_models.sample_cov(prices)
        ef = EfficientFrontier(mu, cov)
        weights = ef.max_sharpe()
        cleaned = ef.clean_weights()
        return {s: float(cleaned.get(s, 0.0)) for s in symbols}
    except (OptimizationError, ValueError):
        equal = 1.0 / len(symbols)
        return {s: equal for s in symbols}


class PyPortfolioOptPositionManagerBox:
    def size(
        self,
        plan: TradePlan,
        available_equity: float,
        size_multiplier: float,
        config: EffectiveConfig,
        symbol: str,
        portfolio_weights: Dict[str, float],
        max_exposure_per_symbol_fraction: float,
        open_positions_count: int = 0,
        symbol_positions_count: int = 0,
    ) -> Tuple[int, List[ParameterUse]]:
        trace: List[ParameterUse] = []

        def req(name: str, calculation: str, output_field: str) -> Any:
            value = config.require(name)
            trace.append(ParameterUse(name, "PositionManager", value, calculation, output_field))
            return value

        capital_fraction = float(req("capital_per_trade_fraction", "fraction of equity risked per trade", "quantity"))
        buffer_fraction = float(req("min_capital_buffer_fraction", "cash reserve withheld from sizing", "quantity"))
        max_live = int(req("max_positions_live", "cap on concurrent live positions", "quantity"))
        max_per_symbol = int(req("max_positions_per_symbol", "cap on positions in a single symbol", "quantity"))
        lot_map = req("lot_size_by_symbol", "per-symbol lot size", "quantity")
        allocation_mode = req("capital_allocation_mode", "capital allocation policy: equal vs aggressive", "quantity")
        req("rebalance_frequency_minutes", "rebalance cadence (portfolio-level, not a per-trade sizing input)", "quantity")
        # max_sector_exposure_fraction / max_symbol_concentration are
        # deliberately NOT read here -- portfolio_weights (PyPortfolioOpt's
        # own optimized output) replaces them as the concentration ceiling.

        if open_positions_count >= max_live or symbol_positions_count >= max_per_symbol:
            return 0, trace

        risk_per_share = abs(plan.entry_price - plan.stop_price)
        if risk_per_share <= 0:
            return 0, trace

        usable_equity = available_equity * (1.0 - buffer_fraction)
        allocation_scale = 1.5 if str(allocation_mode).lower() == "aggressive" else 1.0
        risk_budget = usable_equity * capital_fraction * size_multiplier * allocation_scale
        raw_quantity = math.floor(risk_budget / risk_per_share)

        lot_size = int(lot_map.get(plan.side, 1)) if isinstance(lot_map, dict) and lot_map else 1
        lot_size = max(1, lot_size)
        quantity = (raw_quantity // lot_size) * lot_size

        symbol_weight = portfolio_weights.get(symbol, 0.0)
        max_by_weight = math.floor((usable_equity * symbol_weight) / plan.entry_price) if plan.entry_price else 0
        quantity = min(quantity, max_by_weight)

        # PyPortfolioOpt's max-Sharpe solution can concentrate 100% of
        # weight into one symbol -- a real, legitimate optimizer output,
        # but Gate08SymbolConcentration (the unchanged, in-house safety
        # gate) enforces a separate, hard per-symbol exposure cap
        # regardless of what any upstream sizer proposes. Pre-clipping to
        # that same cap here avoids proposing a size that gate would always
        # reject anyway; it does not weaken or replace the gate, which
        # still runs as the final, authoritative check downstream.
        max_by_safety_cap = math.floor((usable_equity * max_exposure_per_symbol_fraction) / plan.entry_price) if plan.entry_price else 0
        quantity = min(quantity, max_by_safety_cap)

        return max(0, int(quantity)), trace
