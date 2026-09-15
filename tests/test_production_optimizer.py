from production_optimizer import ProductionOptimizer


def test_optimizer_builds_candidates_and_windows():
    opt = ProductionOptimizer(symbol='ADANIENT', seed=7)
    candidates = opt.build_candidates(count=4)
    assert len(candidates) == 4
    assert all('entry_confidence_threshold' in c for c in candidates)
    assert 'train' in opt.windows and 'validation' in opt.windows


def test_optimizer_scores_real_trade_results_and_keeps_test_hidden():
    opt = ProductionOptimizer(symbol='ADANIENT', seed=7)
    df = opt.load_symbol_data()
    s = opt.split_windows(df)
    assert set(s.keys()) == {'warmup', 'train', 'validation', 'test'}
    assert len(s['train']) > 0 and len(s['validation']) > 0 and len(s['test']) > 0
    assert len(df) > len(s['train']) + len(s['validation']) + len(s['test'])

    base = opt.default_config()
    base['entry_confidence_threshold'] = 0.55
    score = opt.evaluate_candidate(base, s['train'])
    assert 'score' in score
    assert 'trade_count' in score
    assert score['trade_count'] >= 0


def test_optimizer_is_deterministic_and_freezes_validation_winner():
    opt1 = ProductionOptimizer(symbol='ADANIENT', seed=123)
    opt2 = ProductionOptimizer(symbol='ADANIENT', seed=123)
    cfg1 = opt1.run(candidate_count=4)
    cfg2 = opt2.run(candidate_count=4)
    assert cfg1['best_candidate']['entry_confidence_threshold'] == cfg2['best_candidate']['entry_confidence_threshold']
    assert cfg1['frozen_config_path'] == cfg2['frozen_config_path']
    assert 'test' not in cfg1['selected_on']
