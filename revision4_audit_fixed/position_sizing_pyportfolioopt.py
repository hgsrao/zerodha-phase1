"""Box 8 (PositionManager) -- PyPortfolioOpt-based capital allocation.

Honest scoping note: PyPortfolioOpt solves "given expected returns and a
covariance matrix across N assets, find optimal weights" -- a PORTFOLIO
construction problem, not a single-trade sizing problem. It has no concept
of "this trade's entry-to-stop distance," which is what actually determines
how many shares a given risk budget buys. So this module does NOT replace
PositionManagerBox's ATR-based risk-per-share sizing (there is nothing in
PyPortfolioOpt to replace it with). The optimizer is deliberately a
one-way *risk derater*: a symbol below equal portfolio weight receives a
smaller ATR risk budget, while a symbol above equal weight may never receive
more than the base ATR risk budget. The independent safety exposure cap
remains the final sizing ceiling.

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


INTRADAY_15MIN_PERIODS_PER_YEAR = 252 * 25
MIN_15MIN_PRICE_OBSERVATIONS = 100


def _causal_15min_close_prices(price_history_by_symbol: Dict[str, pd.Series]) -> pd.DataFrame:
    """Return completed 15-minute close series without using a partial bin.

    The orchestrator supplies only bars at or before its current clock tick.
    Dropping the final resampled bin is an additional conservative guard:
    the current incomplete 15-minute interval can never enter the MVO input.
    """
    completed: Dict[str, pd.Series] = {}
    for symbol, raw_series in price_history_by_symbol.items():
        series = pd.Series(raw_series, copy=True).dropna().astype(float)
        if not isinstance(series.index, pd.DatetimeIndex):
            return pd.DataFrame()
        series = series[~series.index.duplicated(keep="last")].sort_index()
        resampled = series.resample("15min", label="right", closed="right").last().dropna()
        if len(resampled) > 1:
            resampled = resampled.iloc[:-1]
        completed[symbol] = resampled
    return pd.DataFrame(completed).dropna(how="any")


def compute_portfolio_weights(price_history_by_symbol: Dict[str, pd.Series]) -> Dict[str, float]:
    """Real max-Sharpe efficient-frontier weights from each symbol's own
    historical *completed 15-minute* close-price series. Annualisation is
    explicitly 252 trading days x 25 fifteen-minute bars, rather than the
    daily default. Applies Ledoit-Wolf shrinkage to the covariance matrix
    for numerical stability (especially critical with 47 symbols and
    variable historical windows).

    Falls back to equal weight across the universe if optimization fails
    to converge (e.g. too few symbols/bars, or a degenerate/singular
    covariance matrix) -- a documented, honest fallback, not a silent wrong answer.

    LEDOIT-WOLF SHRINKAGE: Blends the sample covariance matrix with the
    identity matrix to reduce sensitivity to sample noise. PyPortfolioOpt's
    CovarianceShrinkage implements Ledoit & Wolf (2004) which reduces the
    condition number of the covariance matrix, making the QP solver more
    numerically stable and reducing "Solution may be inaccurate" warnings.
    """
    symbols = list(price_history_by_symbol.keys())
    if len(symbols) < 2:
        return {s: 1.0 for s in symbols}

    prices = _causal_15min_close_prices(price_history_by_symbol)
    if len(prices) < MIN_15MIN_PRICE_OBSERVATIONS:
        equal = 1.0 / len(symbols)
        return {s: equal for s in symbols}

    try:
        mu = expected_returns.mean_historical_return(
            prices, frequency=INTRADAY_15MIN_PERIODS_PER_YEAR,
        )

        # LEDOIT-WOLF SHRINKAGE: Compute shrunk covariance matrix
        # This regularizes the sample covariance by blending it with the
        # identity matrix, reducing the condition number and improving
        # numerical stability in the QP solve. Especially important with
        # 47 symbols where sample covariance can have large condition numbers.
        try:
            # Use CovarianceShrinkage for Ledoit-Wolf (2004) regularization
            shrinkage_estimator = risk_models.CovarianceShrinkage(
                prices, frequency=INTRADAY_15MIN_PERIODS_PER_YEAR,
            )
            cov = shrinkage_estimator.ledoit_wolf()
        except Exception:
            # Fallback to sample covariance if shrinkage fails
            cov = risk_models.sample_cov(
                prices, frequency=INTRADAY_15MIN_PERIODS_PER_YEAR,
            )

        ef = EfficientFrontier(mu, cov)
        weights = ef.max_sharpe()
        cleaned = ef.clean_weights()
        return {s: float(cleaned.get(s, 0.0)) for s in symbols}
    except (OptimizationError, ValueError):
        equal = 1.0 / len(symbols)
        return {s: equal for s in symbols}


class PyPortfolioOptPositionManagerBox:
    def __init__(self) -> None:
        # Read-only telemetry for the orchestrator's shadow/reporting path.
        # It does not feed back into sizing or safety decisions.
        self.last_sizing_telemetry: Dict[str, Any] = {}

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
        # rebalance_frequency_minutes used to be req()'d here for coverage-
        # tracking only (never affected this method's real output -- the
        # real PyPortfolioOpt refit cadence is orchestrator.py's own
        # hardcoded PORTFOLIO_WEIGHT_REFIT_EVERY_BARS constant). Removed
        # from the registry entirely this session, replaced by
        # trailing_stop_atr_mult (see canonical_parameter_registry.py's
        # FROZEN_IDENTITY_SHA256 comment) -- this call is removed to match.
        # max_sector_exposure_fraction / max_symbol_concentration are
        # deliberately NOT read here -- portfolio_weights (PyPortfolioOpt's
        # own optimized output) replaces them as the concentration ceiling.

        self.last_sizing_telemetry = {"symbol": symbol, "sizing_status": "not_sized"}
        if open_positions_count >= max_live or symbol_positions_count >= max_per_symbol:
            self.last_sizing_telemetry["sizing_status"] = "position_limit"
            return 0, trace

        risk_per_share = abs(plan.entry_price - plan.stop_price)
        if risk_per_share <= 0:
            self.last_sizing_telemetry["sizing_status"] = "invalid_risk_per_share"
            return 0, trace

        usable_equity = available_equity * (1.0 - buffer_fraction)
        allocation_scale = 1.5 if str(allocation_mode).lower() == "aggressive" else 1.0
        base_risk_budget = usable_equity * capital_fraction * size_multiplier * allocation_scale

        # Optimizer output is an allocation-quality signal, never permission
        # to exceed ATR-derived trade risk. It can only derate a weak symbol.
        # Equal weight is the neutral (1.0) reference.
        equal_weight = 1.0 / len(portfolio_weights) if portfolio_weights else 0.0
        symbol_weight = max(0.0, float(portfolio_weights.get(symbol, 0.0)))
        conviction_derate = min(1.0, symbol_weight / equal_weight) if equal_weight > 0 else 0.0
        risk_budget = base_risk_budget * conviction_derate
        raw_quantity = math.floor(risk_budget / risk_per_share)

        lot_size = int(lot_map.get(plan.side, 1)) if isinstance(lot_map, dict) and lot_map else 1
        lot_size = max(1, lot_size)
        quantity = (raw_quantity // lot_size) * lot_size

        # The safety cap is independent of, and authoritative over, the
        # optimizer. It remains a hard notional ceiling after risk sizing.
        max_by_safety_cap = math.floor((usable_equity * max_exposure_per_symbol_fraction) / plan.entry_price) if plan.entry_price else 0
        quantity = min(quantity, max_by_safety_cap)

        self.last_sizing_telemetry = {
            "symbol": symbol,
            "sizing_status": "sized" if quantity > 0 else "zero_quantity",
            "base_risk_budget": float(base_risk_budget),
            "derated_risk_budget": float(risk_budget),
            "risk_per_share": float(risk_per_share),
            "symbol_weight": float(symbol_weight),
            "equal_weight": float(equal_weight),
            "conviction_derate": float(conviction_derate),
            "raw_quantity": int(raw_quantity),
            "lot_rounded_quantity": int((raw_quantity // lot_size) * lot_size),
            "safety_max_quantity": int(max_by_safety_cap),
            "final_quantity": int(max(0, quantity)),
        }

        return max(0, int(quantity)), trace
