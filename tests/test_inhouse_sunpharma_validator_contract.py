from validate_inhouse_sunpharma_sealed import SYMBOL, WARMUP


def test_real_sunpharma_validator_contract_is_explicit():
    assert SYMBOL == "SUNPHARMA"
    assert WARMUP == 60
