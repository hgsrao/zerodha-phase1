import sys
sys.path.insert(0, ".")

from revision2.contracts import PASignal
from revision2_external.chart_studies_confirmation import blend_pa_and_chart_studies


def _pa_signal(direction=1, confidence=0.6, exit_confidence=0.5):
    return PASignal(symbol="TEST", timestamp="2024-01-02 09:20", direction=direction, confidence=confidence,
                     momentum=0.3, volatility=0.01, vwap_deviation=0.2, volume_confirmation=0.4,
                     exit_confidence=exit_confidence, quality_band="amber")


def test_agreement_averages_the_two_confidences():
    pa = _pa_signal(direction=1, confidence=0.6, exit_confidence=0.5)
    composite = {"confidence": 0.8, "direction": 1}
    blended = blend_pa_and_chart_studies(pa, composite)
    assert blended.confidence == (0.6 + 0.8) / 2.0
    assert blended.exit_confidence == (0.5 + 0.8) / 2.0
    assert blended.direction == 1, "direction must stay PA's own"


def test_disagreement_floors_to_the_lower_of_the_two_not_an_average():
    pa = _pa_signal(direction=1, confidence=0.8, exit_confidence=0.7)
    composite = {"confidence": 0.3, "direction": -1}  # real disagreement
    blended = blend_pa_and_chart_studies(pa, composite)
    assert blended.confidence == 0.3, "a real disagreement must not look more confident than the weaker signal"
    assert blended.exit_confidence == 0.3
    assert blended.direction == 1, "direction stays PA's own even on disagreement"


def test_disagreement_floor_works_the_other_direction_too():
    # PA weak, composite strong but opposed -- the floor must still pick
    # the lower value regardless of which side (PA or composite) is lower.
    pa = _pa_signal(direction=1, confidence=0.2, exit_confidence=0.3)
    composite = {"confidence": 0.9, "direction": -1}
    blended = blend_pa_and_chart_studies(pa, composite)
    assert blended.confidence == 0.2
    assert blended.exit_confidence == 0.3


def test_neutral_composite_direction_counts_as_disagreement_with_a_directional_pa_signal():
    pa = _pa_signal(direction=1, confidence=0.7, exit_confidence=0.6)
    composite = {"confidence": 0.9, "direction": 0}
    blended = blend_pa_and_chart_studies(pa, composite)
    assert blended.confidence == 0.7, "a neutral composite read is not real agreement -- must not average up"


def test_only_confidence_fields_change_everything_else_is_preserved():
    pa = _pa_signal()
    composite = {"confidence": 0.5, "direction": 1}
    blended = blend_pa_and_chart_studies(pa, composite)
    assert blended.symbol == pa.symbol
    assert blended.timestamp == pa.timestamp
    assert blended.momentum == pa.momentum
    assert blended.volatility == pa.volatility
    assert blended.vwap_deviation == pa.vwap_deviation
    assert blended.volume_confirmation == pa.volume_confirmation
    assert blended.quality_band == pa.quality_band
