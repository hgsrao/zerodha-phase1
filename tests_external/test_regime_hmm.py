import sys
sys.path.insert(0, ".")

import numpy as np

from revision2_external.regime_hmm import GaussianHMM


def _two_regime_data(seed=3, n_per_regime=300):
    rng = np.random.default_rng(seed)
    # Regime 0: low mean, low variance ("calm"). Regime 1: high mean, high
    # variance ("volatile"). True labels kept for a real recovery check.
    calm = rng.normal(loc=0.0, scale=0.5, size=(n_per_regime, 1))
    volatile = rng.normal(loc=5.0, scale=2.0, size=(n_per_regime, 1))
    X = np.vstack([calm, volatile, calm, volatile])
    true_labels = np.concatenate([
        np.zeros(n_per_regime), np.ones(n_per_regime),
        np.zeros(n_per_regime), np.ones(n_per_regime),
    ])
    return X, true_labels


def test_log_likelihood_is_non_decreasing_across_em_iterations():
    X, _ = _two_regime_data()
    model = GaussianHMM(n_states=2, n_iter=30, random_state=1)
    model.fit(X)
    lls = model.monitor_
    assert len(lls) > 1
    # EM guarantees monotonic non-decrease up to floating point noise.
    diffs = np.diff(lls)
    assert (diffs >= -1e-6).all(), f"log-likelihood decreased: {lls}"


def test_recovers_the_two_true_regimes_from_synthetic_data():
    X, true_labels = _two_regime_data()
    model = GaussianHMM(n_states=2, n_iter=50, random_state=1)
    model.fit(X)
    predicted = model.predict(X)

    # State labeling is arbitrary (0/1 could be swapped) -- check agreement
    # against both possible label mappings and take the better one.
    agreement_a = (predicted == true_labels).mean()
    agreement_b = (predicted == (1 - true_labels)).mean()
    best_agreement = max(agreement_a, agreement_b)
    assert best_agreement > 0.90, f"expected >90% regime recovery, got {best_agreement:.2%}"

    # The fitted means should be well-separated and roughly match the true
    # regime means (0.0 and 5.0), in some order.
    fitted_means = sorted(model.means_.flatten())
    assert fitted_means[0] < 1.5
    assert fitted_means[1] > 3.5


def test_real_market_returns_produce_a_stable_two_state_fit():
    import pandas as pd
    from market_data_loader import MarketDataLoader
    from revision2.dataset_manifest import DatasetManifest

    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    frame = loader._load_symbol_csv("ADANIENT").tail(3000).reset_index(drop=True)

    returns = frame["close"].pct_change().dropna().to_numpy()[:, None] * 100
    rolling_vol = pd.Series(returns.flatten()).rolling(20).std().dropna().to_numpy()[:, None]
    features = np.hstack([returns[-len(rolling_vol):], rolling_vol])

    model = GaussianHMM(n_states=2, n_iter=40, random_state=2)
    model.fit(features)
    states = model.predict(features)

    assert set(np.unique(states)) == {0, 1}, "expected both regimes to actually occur in real data"
    # The two states should have genuinely different volatility profiles --
    # otherwise the model collapsed to one regime wearing two labels.
    vol_by_state = [features[states == s, 1].mean() for s in (0, 1)]
    assert abs(vol_by_state[0] - vol_by_state[1]) > 1e-6
