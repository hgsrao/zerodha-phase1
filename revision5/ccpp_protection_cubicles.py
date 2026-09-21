"""
CCPP Dual-Engine Protection Cubicles & Excitation Regulators (ANSI Standard)
=============================================================================
Tier 1: Master Substation Grid Protection (MiCOM P44x equivalent)
        ANSI 81O/81U, ANSI 21, ANSI 67/67N, ANSI 25
Tier 2: Dedicated Turbine-Generator Bay Protection (SEL-300G equivalent)
        ANSI 87G, ANSI 40, ANSI 32R, ANSI 24, ANSI 46, ANSI 60FL
Tier 3: Dedicated Static Exciter / Automatic Voltage Regulator (AVR / DECS)
        Terminal Voltage (Notional Sizing), UEL, OEL Limiters
"""

import numpy as np
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# TIER 1: MASTER SUBSTATION / NIFTY 50 GRID PROTECTION (ANSI 81/21/67/25)
# ---------------------------------------------------------------------------

@dataclass
class GridRelayTrip:
    tripped: bool
    ansi_code: str
    reason: str

class MasterGridProtectionMiCOM:
    """
    Substation Intertie Protection for the entire 400kV Busbar (Portfolio Margin).
    Monitors Nifty 50 frequency stability and global fleet risk.
    """
    def __init__(
        self,
        max_daily_fleet_drawdown_pct: float = 0.020,  # ANSI 67: 2.0% fleet hard trip
        nifty_crash_rate_pct: float = 0.015,          # ANSI 81U: 1.5% drop in 15 mins
        max_regime_vol_z: float = 3.0                 # ANSI 21: Distance / Zone shock
    ):
        self.max_dd_pct = max_daily_fleet_drawdown_pct
        self.nifty_crash_rate = nifty_crash_rate_pct
        self.max_regime_vol_z = max_regime_vol_z
        self.master_breaker_open = False

    def evaluate_grid_intertie(
        self,
        nifty_15m_return: float,
        nifty_vol_z: float,
        fleet_equity_drawdown_pct: float
    ) -> GridRelayTrip:
        # ANSI 67 / 67N: Directional Overcurrent (Cascading Margin Runaway)
        if fleet_equity_drawdown_pct >= self.max_dd_pct:
            self.master_breaker_open = True
            return GridRelayTrip(
                tripped=True,
                ansi_code="ANSI 67",
                reason=f"Directional Overcurrent: Fleet Drawdown {fleet_equity_drawdown_pct*100:.2f}% >= {self.max_dd_pct*100:.2f}%"
            )

        # ANSI 81U / 81O: Under / Over Frequency (Macro Index Crash / Flash Spike)
        if nifty_15m_return <= -self.nifty_crash_rate:
            return GridRelayTrip(
                tripped=True,
                ansi_code="ANSI 81U",
                reason=f"Under-Frequency: NIFTY 15m collapse {nifty_15m_return*100:.2f}% <= -{self.nifty_crash_rate*100:.2f}%"
            )
        if nifty_15m_return >= self.nifty_crash_rate * 1.5:
            return GridRelayTrip(
                tripped=True,
                ansi_code="ANSI 81O",
                reason=f"Over-Frequency: NIFTY 15m melt-up {nifty_15m_return*100:.2f}%"
            )

        # ANSI 21: Distance Protection (Zone 1 Systemic Liquidity Shock)
        if abs(nifty_vol_z) >= self.max_regime_vol_z:
            return GridRelayTrip(
                tripped=True,
                ansi_code="ANSI 21",
                reason=f"Distance Zone 1: Volatility shock Z={nifty_vol_z:.2f} outside safe corridor"
            )

        return GridRelayTrip(tripped=False, ansi_code="NORMAL", reason="Grid Synchronized")


# ---------------------------------------------------------------------------
# TIER 2: INDIVIDUAL BAY AUTOMATIC VOLTAGE REGULATOR (AVR / DECS)
# ---------------------------------------------------------------------------

class BayExcitationAVR:
    """
    Dedicated Static Exciter per Turbine Bay.
    Translates active megawatt demands into reactive terminal voltage (lot size).
    Guards boundaries with Under-Excitation (UEL) & Over-Excitation (OEL) Limiters.
    """
    def __init__(
        self,
        bay_name: str,
        allocated_capital_inr: float,
        min_notional_uel_inr: float = 25000.0,
        max_notional_oel_inr: float = 800000.0
    ):
        self.bay_name = bay_name
        self.allocated_capital = allocated_capital_inr
        self.uel_min = min_notional_uel_inr
        self.oel_max = max_notional_oel_inr

    def calculate_lot_size(
        self,
        asset_price: float,
        asset_atr: float,
        z_strength: float
    ) -> Tuple[int, str]:
        if asset_price <= 0 or asset_atr <= 0:
            return 0, "AVR: Zero Price / ATR Fault"

        vol_scalar = max(0.2, min(2.0, 1.0 / (asset_atr / asset_price * 100.0)))
        raw_notional = (self.allocated_capital * 0.15) * vol_scalar * min(1.5, abs(z_strength) / 1.8)

        # OEL: Over-Excitation Limiter (Cap Leverage)
        if raw_notional > self.oel_max:
            clamped_notional = self.oel_max
            limiter_state = "OEL_ACTIVE"
        # UEL: Under-Excitation Limiter (Floor Friction)
        elif raw_notional < self.uel_min:
            clamped_notional = 0
            return 0, "UEL_LOCKOUT: Below Min Commercial Friction"
        else:
            clamped_notional = raw_notional
            limiter_state = "AVR_LINEAR"

        qty = int(clamped_notional / asset_price)
        return qty, limiter_state


# ---------------------------------------------------------------------------
# TIER 3: INDIVIDUAL BAY GENERATOR UNIT PROTECTION (ANSI MATRIX SEL-300G)
# ---------------------------------------------------------------------------

@dataclass
class UnitRelayTrip:
    tripped: bool
    ansi_code: str
    symbol: str
    reason: str

class BayUnitProtectionSEL300G:
    """
    Dedicated Multifunction Generator Protection Relay per Turbine Bay.
    Isolates single symbols without tripping the rest of the bay or fleet.
    """
    def __init__(
        self,
        bay_name: str,
        max_slippage_pct: float = 0.0035,   # ANSI 87G: 0.35% Diff Trip
        max_adverse_bars: int = 4,          # ANSI 32R: Negative drift timeout
        vol_surge_mult: float = 3.5         # ANSI 24: Volts/Hz Overfluxing
    ):
        self.bay_name = bay_name
        self.max_slippage_pct = max_slippage_pct
        self.max_adverse_bars = max_adverse_bars
        self.vol_surge_mult = vol_surge_mult
        self.adverse_drift_counters: Dict[str, int] = {}

    def check_pre_synchronization(
        self,
        symbol: str,
        bid: float,
        ask: float,
        last_tick_age_sec: float,
        hist_atr: float,
        curr_bar_range: float
    ) -> UnitRelayTrip:
        # ANSI 60FL: Potential Transformer (PT) Fuse Failure / Stale Data Loss
        if last_tick_age_sec > 120.0:
            return UnitRelayTrip(
                tripped=True,
                ansi_code="ANSI 60FL",
                symbol=symbol,
                reason=f"PT Fuse Failure: Tick age {last_tick_age_sec:.1f}s exceeds 120s limit"
            )

        # ANSI 40: Loss of Field / Severe Liquidity Evaporation
        spread_pct = (ask - bid) / (bid + 1e-9)
        if spread_pct > 0.005:
            return UnitRelayTrip(
                tripped=True,
                ansi_code="ANSI 40",
                symbol=symbol,
                reason=f"Loss of Excitation: Illiquid spread {spread_pct*100:.2f}% > 0.50%"
            )

        # ANSI 24: Volts / Hertz Overfluxing (Extreme Intra-Bar Volatility Spike)
        if hist_atr > 0 and (curr_bar_range / hist_atr) > self.vol_surge_mult:
            return UnitRelayTrip(
                tripped=True,
                ansi_code="ANSI 24",
                symbol=symbol,
                reason=f"V/Hz Overfluxing: Bar range {curr_bar_range / hist_atr:.1f}x ATR > {self.vol_surge_mult}x"
            )

        return UnitRelayTrip(tripped=False, ansi_code="NORMAL", symbol=symbol, reason="Ready")

    def check_in_flight_protection(
        self,
        symbol: str,
        entry_price: float,
        current_price: float,
        exec_fill_price: float,
        intended_price: float
    ) -> UnitRelayTrip:
        # ANSI 87G: Differential Protection (Instantaneous Execution Anomaly)
        slippage = abs(exec_fill_price - intended_price) / intended_price
        if slippage > self.max_slippage_pct:
            return UnitRelayTrip(
                tripped=True,
                ansi_code="ANSI 87G",
                symbol=symbol,
                reason=f"Stator Differential Trip: Slippage {slippage*100:.2f}% > {self.max_slippage_pct*100:.2f}%"
            )

        # ANSI 32R: Reverse Power Relay (Turbine Motorization / Adverse Drift)
        if current_price < entry_price:
            self.adverse_drift_counters[symbol] = self.adverse_drift_counters.get(symbol, 0) + 1
            if self.adverse_drift_counters[symbol] >= self.max_adverse_bars:
                return UnitRelayTrip(
                    tripped=True,
                    ansi_code="ANSI 32R",
                    symbol=symbol,
                    reason=f"Reverse Power Trip: Continuous motorization for {self.max_adverse_bars} intervals"
                )
        else:
            self.adverse_drift_counters[symbol] = 0

        return UnitRelayTrip(tripped=False, ansi_code="NORMAL", symbol=symbol, reason="Operating")
