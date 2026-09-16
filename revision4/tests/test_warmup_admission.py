import pandas as pd

from revision4.warmup_admission import assess_warmup


def _bars(*, flat: bool) -> pd.DataFrame:
    count = 60
    close = [100.0] * count if flat else [100.0 + index * 0.1 for index in range(count)]
    return pd.DataFrame({
        "open": close,
        "high": close if flat else [value + 0.2 for value in close],
        "low": close if flat else [value - 0.2 for value in close],
        "close": close,
        "volume": [1_000] * count if flat else [1_000 + index * 10 for index in range(count)],
    })


def test_flat_warmup_is_rejected_without_fallback_scales():
    result = assess_warmup("JIOFIN", _bars(flat=True))

    assert not result.admitted
    assert "degenerate_return_scale" in result.reasons
    assert "degenerate_atr_scale" in result.reasons


def test_variable_warmup_is_admitted():
    result = assess_warmup("SUNPHARMA", _bars(flat=False))

    assert result.admitted
    assert result.reasons == ()
    assert result.return_std and result.return_std > 0
    assert result.atr_mean and result.atr_mean > 0
