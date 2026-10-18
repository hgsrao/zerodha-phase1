"""Box 4b -- the Chart-Studies Confirmation Layer: blends PA's (Box 4)
own, already-finished confidence with CompositeStudySignal's independent,
PID-weighted confidence -- an overlay ON Box 4's output, not a
replacement for it, and not a new numbered peer of Box 5-10.

PA already blends momentum, VWAP deviation, volatility, and volume
confirmation internally, using its own existing weights
(momentum_weight/vwap_weight/volatility_weight/confirmation_2bar_weight).
This module does NOT touch or re-derive any of that -- it takes PA's
ALREADY-FINISHED confidence number and blends it with a second,
independently-computed confidence number (from Ichimoku/Bollinger/
Stochastic/session VWAP, PID-weighted -- see composite_study_signal.py)
at the very end, right before ID ever sees the result. A blend of two
finished opinions, not a merge of raw ingredients.

Real, disclosed rule, simple and not swept/optimized -- same discipline
this project already uses for its other first-cut thresholds (e.g. the
original chart-studies monitor's +3/0 entry/exit thresholds):
- If PA's direction and the composite's direction AGREE: average the two
  confidences (equal weight -- both are now real, live, independently-
  computed signals, and agreement is itself informative).
- If they DISAGREE: floor the blended confidence to whichever of the two
  is LOWER, not an average -- a real disagreement between two
  independent signals should never look MORE confident than either
  signal alone.
- Direction is left as PA's own. The rest of the pipeline (MPC's stop/
  target side) is built around PA's direction convention; a real
  disagreement is expressed through the lowered confidence instead, which
  ID's existing entry_confidence_threshold already acts on -- no new gate
  needed for this.

Deliberately NOT wired into the orchestrator yet -- built and tested
standalone first, the same way every other real component on this branch
was proven before an integration decision.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict

from revision2.contracts import PASignal


def blend_pa_and_chart_studies(pa_signal: PASignal, composite_result: Dict[str, Any]) -> PASignal:
    composite_confidence = float(composite_result["confidence"])
    composite_direction = int(composite_result["direction"])

    if pa_signal.direction == composite_direction:
        blended_confidence = (pa_signal.confidence + composite_confidence) / 2.0
        blended_exit_confidence = (pa_signal.exit_confidence + composite_confidence) / 2.0
    else:
        blended_confidence = min(pa_signal.confidence, composite_confidence)
        blended_exit_confidence = min(pa_signal.exit_confidence, composite_confidence)

    return replace(pa_signal, confidence=blended_confidence, exit_confidence=blended_exit_confidence)
