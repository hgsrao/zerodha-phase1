import numpy as np

from revision2_external.regime_hmm import GaussianHMM


def test_hmm_streaming_filter_can_continue_from_a_train_period_without_future_rows():
    rng = np.random.default_rng(7)
    train = rng.normal(0.0, 0.5, size=(100, 2))
    replay = rng.normal(0.0, 0.5, size=(20, 2))
    model = GaussianHMM(n_states=2, n_iter=10, random_state=2).fit(train)
    prior = model.filter_proba(train)[-1]
    posteriors = []
    for row in replay:
        prior = model.filter_step(row, prior)
        posteriors.append(prior)
    assert np.allclose(np.asarray(posteriors).sum(axis=1), 1.0)
