"""Causal price/volume/phase/studies reversal fusion, shadow-only.

Inputs are the completed bar's price rate dP/dt, volume rate dV/dt, a
single-symbol Hilbert phase-coherence check, and the existing fast chart-study
reversal vote.  Phase is deliberately not assigned a universal direction:
its absolute reference varies with dominant-cycle estimation. Instead it
confirms that the local curve is rotating smoothly while price and studies
provide the reversal direction.
"""
from __future__ import annotations

from typing import Any, Dict
import numpy as np
import pandas as pd
import talib

from revision2_external.study_entry_shadow import StudyEntryShadowLedger


def _wrap(current: float, previous: float) -> float:
    return float((current - previous + 180.0) % 360.0 - 180.0)


class PriceVolumeStudyReversalShadow:
    def __init__(self, ledger: StudyEntryShadowLedger, minimum_history: int = 64) -> None:
        self.ledger = ledger
        self.minimum_history = max(64, int(minimum_history))

    def observe(self, symbol: str, index: int, timestamp: object, bar: Any, history: pd.DataFrame, studies: Dict[str, Any], atr: float) -> Dict[str, Any]:
        close = history.close.to_numpy(float); high = history.high.to_numpy(float); low = history.low.to_numpy(float); volume = history.volume.to_numpy(float)
        obs: Dict[str, Any] = {"timestamp": str(timestamp), "symbol": symbol, "index": index, "sensor": "price_volume_phase_studies_reversal", "ready": False}
        if len(close) < self.minimum_history:
            self.ledger.observations.append(obs); return obs
        scale = max(float(atr), 1e-6)
        price_rate = float((close[-1] - close[-2]) / scale)
        previous_price_rate = float((close[-2] - close[-3]) / scale)
        recent_volume = volume[-21:-1]
        volume_baseline = max(float(np.median(recent_volume)), 1e-12)
        volume_rate = float((volume[-1] - volume[-2]) / volume_baseline)
        volume_ratio = float(volume[-1] / volume_baseline)
        bar_range = max(float(high[-1] - low[-1]), 1e-12)
        close_location = float((close[-1] - low[-1]) / bar_range)
        phase_series = talib.HT_DCPHASE(close)
        phase, prior_phase = float(phase_series[-1]), float(phase_series[-2])
        phase_velocity = _wrap(phase, prior_phase)
        phase_coherent = bool(np.isfinite(phase) and np.isfinite(prior_phase) and 1.0 <= abs(phase_velocity) <= 30.0)
        votes = dict(studies["votes"])
        side = None
        # dP/dt establishes a causal sign reversal. dV/dt must show expanding
        # participation, the fast study must agree, and phase must be stable.
        if previous_price_rate <= 0 < price_rate and volume_rate > 0 and volume_ratio >= 1.0 and close_location >= .60 and votes.get("stochastic") == 1 and phase_coherent:
            side = "BUY"
        elif previous_price_rate >= 0 > price_rate and volume_rate > 0 and volume_ratio >= 1.0 and close_location <= .40 and votes.get("stochastic") == -1 and phase_coherent:
            side = "SELL"
        obs.update({
            "ready": True, "dprice_dt_atr": price_rate, "prior_dprice_dt_atr": previous_price_rate,
            "dvolume_dt_relative": volume_rate, "volume_ratio": volume_ratio, "close_location": close_location,
            "phase_angle_degrees": phase if np.isfinite(phase) else None, "phase_velocity_degrees_per_bar": phase_velocity if np.isfinite(phase_velocity) else None,
            "phase_coherent": phase_coherent, "study_votes": votes, "study_composite_direction": studies["direction"],
            "study_composite_confidence": studies["confidence"], "reversal_side": side,
        })
        self.ledger.observations.append(obs)
        if side:
            self.ledger.schedule(symbol=symbol, index=index, side=side, setup_extreme=float(low[-1] if side == "BUY" else high[-1]), atr=scale, observation=obs)
        return obs
