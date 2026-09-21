"""
Revision 5 Heat Recovery Steam Generator (HRSG) Subsystem
=========================================================
Thermodynamic Analogue:
  Captures surplus margin / exhaust capital from high-cycling gas turbine bays
  (GTG1_HEAVY_INDUSTRY, GTG2_TECH_TELECOM) and injects regulated steam pressure
  into baseload / damped steam turbine bays (CSTG1_BFSI, CSTG2_CONSUMER_AUTO, BPSTG_HEALTHCARE).

Core Responsibilities:
  1. Exhaust Capital Recovery:
     Reallocates idle capacity when GTGs trip or enter dampening cooldowns.
  2. Economizer Covariance Damper:
     Pinch-point correlation monitor: penalizes bay weights when cross-bay
     pairwise correlation rho exceeds the critical threshold (rho_crit = 0.65).
  3. Superheater Excursion Boost:
     Grants controlled AVR lot-sizing headroom to high-expectancy STG bays.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Tuple
import numpy as np

from revision5.topology import (
    BAY_IDS,
    GTG1_HEAVY_INDUSTRY,
    GTG2_TECH_TELECOM,
    CSTG1_BFSI,
    CSTG2_CONSUMER_AUTO,
    BPSTG_HEALTHCARE,
)

logger = logging.getLogger("CCPP_HRSG_R5")
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("[%(asctime)s][%(levelname)s][%(name)s] %(message)s"))
    logger.addHandler(ch)
    logger.setLevel(logging.INFO)

GAS_TURBINE_BAYS = (GTG1_HEAVY_INDUSTRY, GTG2_TECH_TELECOM)
STEAM_TURBINE_BAYS = (CSTG1_BFSI, CSTG2_CONSUMER_AUTO, BPSTG_HEALTHCARE)


class HeatRecoverySteamGenerator:
    """CCPP Heat Recovery Steam Generator (HRSG) and Thermal Covariance Balancer."""

    def __init__(
        self,
        base_plant_capital: float = 1_000_000.0,
        rho_crit: float = 0.65,
        max_superheat_boost: float = 0.15,
    ):
        self.base_plant_capital = base_plant_capital
        self.rho_crit = rho_crit
        self.max_superheat_boost = max_superheat_boost

        # Rolling returns buffer per bay for cross-bay correlation tracking
        self.bay_returns: Dict[str, List[float]] = {b: [] for b in BAY_IDS}

    def record_bay_return(self, bay_id: str, ret: float) -> None:
        """Record trade return for correlation tracking."""
        if bay_id in self.bay_returns:
            self.bay_returns[bay_id].append(float(ret))
            if len(self.bay_returns[bay_id]) > 30:
                self.bay_returns[bay_id].pop(0)

    def calculate_cross_bay_correlations(self) -> Dict[Tuple[str, str], float]:
        """Compute rolling pairwise correlation across active bays."""
        correlations: Dict[Tuple[str, str], float] = {}
        for i, bay_a in enumerate(BAY_IDS):
            for bay_b in BAY_IDS[i + 1:]:
                ret_a = self.bay_returns[bay_a]
                ret_b = self.bay_returns[bay_b]
                min_len = min(len(ret_a), len(ret_b))
                if min_len < 6:
                    correlations[(bay_a, bay_b)] = 0.0
                else:
                    arr_a = np.array(ret_a[-min_len:], dtype=np.float64)
                    arr_b = np.array(ret_b[-min_len:], dtype=np.float64)
                    std_a = float(np.std(arr_a))
                    std_b = float(np.std(arr_b))
                    if std_a > 1e-6 and std_b > 1e-6:
                        corr = float(np.corrcoef(arr_a, arr_b)[0, 1])
                        correlations[(bay_a, bay_b)] = float(np.clip(corr, -1.0, 1.0))
                    else:
                        correlations[(bay_a, bay_b)] = 0.0
        return correlations

    def compute_economizer_penalties(self) -> Dict[str, float]:
        """
        Pinch-Point Economizer:
        If cross-bay correlation exceeds rho_crit, compute a dampening factor [0.70, 1.0].
        """
        corrs = self.calculate_cross_bay_correlations()
        penalties = {b: 1.0 for b in BAY_IDS}

        for (bay_a, bay_b), corr in corrs.items():
            if corr > self.rho_crit:
                excess = corr - self.rho_crit
                damper = max(0.70, 1.0 - (excess * 0.80))
                penalties[bay_a] = min(penalties[bay_a], damper)
                penalties[bay_b] = min(penalties[bay_b], damper)
                logger.warning(
                    f"HRSG Economizer Tripped: {bay_a} <-> {bay_b} rho={corr:.2f} > {self.rho_crit}. "
                    f"Applying thermal damper {damper:.2f}"
                )

        return penalties

    def harvest_exhaust_capital(
        self,
        bay_states: Dict[str, dict],
        base_allocations: Dict[str, float],
    ) -> Dict[str, float]:
        """
        Exhaust Capital Recirculation:
        If a GTG bay is offline or cooling down, harvest its unused capital allocation
        and route it into healthy steam turbine (STG) bays according to their baseload weights.
        """
        harvested_capital = 0.0
        adjusted_allocations = dict(base_allocations)

        # 1. Harvest idle exhaust capital from disabled/cooling gas turbines
        for gtg_id in GAS_TURBINE_BAYS:
            state = bay_states.get(gtg_id, {})
            is_offline = state.get("tripped_offline", False)
            in_cooldown = state.get("cooldown_cycles", 0) > 5

            if is_offline or in_cooldown:
                surplus = adjusted_allocations[gtg_id] * 0.60  # Harvest 60% of dormant capital
                adjusted_allocations[gtg_id] -= surplus
                harvested_capital += surplus
                logger.info(
                    f"HRSG Flue-Gas Heat Captured: Harvesting ₹{surplus:,.2f} from {gtg_id} (offline/cooldown)"
                )

        # 2. Inject harvested steam pressure into operational Steam Turbine Bays
        if harvested_capital > 0:
            active_stgs = [
                b for b in STEAM_TURBINE_BAYS
                if not bay_states.get(b, {}).get("tripped_offline", False)
            ]
            if active_stgs:
                stg_share = harvested_capital / len(active_stgs)
                for b in active_stgs:
                    adjusted_allocations[b] += stg_share
                    logger.info(f"HRSG Steam Injection: Routing ₹{stg_share:,.2f} to {b}")

        # 3. Apply Pinch-point economizer penalties
        penalties = self.compute_economizer_penalties()
        for b in BAY_IDS:
            adjusted_allocations[b] *= penalties[b]

        # 4. Normalize sum of allocations to base_plant_capital
        total_adj = sum(adjusted_allocations.values())
        if total_adj > 0:
            scale = self.base_plant_capital / total_adj
            for b in BAY_IDS:
                adjusted_allocations[b] = round(adjusted_allocations[b] * scale, 2)

        return adjusted_allocations

    def get_superheat_headroom(self, bay_id: str, win_rate_r: float) -> float:
        """
        Superheater Duct Firing:
        Expands AVR lot sizing ceiling by up to 15% when a steam bay maintains strong expectancy.
        """
        if bay_id not in STEAM_TURBINE_BAYS:
            return 1.0  # Gas turbines do not receive steam superheat

        if win_rate_r >= 0.60:
            return 1.0 + self.max_superheat_boost
        elif win_rate_r >= 0.45:
            return 1.0 + (self.max_superheat_boost * 0.50)
        return 1.0
