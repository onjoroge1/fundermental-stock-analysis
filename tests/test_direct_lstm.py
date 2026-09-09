import math
import pytest
import stock_machine.prediction as p


def test_direct_targets_end_in_training_slice():
    features = [[float(i), 0.] for i in range(65)]
    xs, ys = p.make_direct_windows(features)
    assert len(xs) == 6
    assert xs[0][-1][0] == 39
    assert ys[0] == [sum(range(40, 40+h))/math.sqrt(h) for h in (5, 10, 20)]
    assert ys[-1][-1] == sum(range(45, 65))/math.sqrt(20)
    assert p.make_direct_windows(features[:59]) == ([], [])


def test_direct_summary_has_no_invented_long_horizons():
    result = p.summarize_direct_samples({5: [-.1, 0, .1], 10: [-.2, 0, .2],
                                       20: [-.3, 0, .3]}, 100)
    assert set(result['horizons']) == {'5d', '10d', '20d'}
    assert [r['day'] for r in result['fan']] == [5, 10, 20]
    assert result['horizons']['20d']['p50'] == 100


@pytest.mark.skipif(not p.TORCH_OK, reason='requires prediction extra')
def test_direct_training_and_inference_single_forward():
    p.torch.set_num_threads(1)
    feats = [[math.sin(i)*.1, .1] for i in range(85)]
    model = p.train_lstm(feats, epochs=1)
    calls = []
    handle = model.register_forward_hook(lambda *args: calls.append(1))
    samples = p.lstm_horizon_samples(model, feats[-40:], .001, .01, n_samples=20)
    handle.remove()
    assert len(calls) == 1
    assert set(samples) == {5, 10, 20}
    assert all(len(v) == 20 and all(math.isfinite(x) for x in v) for v in samples.values())


def test_walk_forward_lstm_never_observes_outcomes(monkeypatch):
    seen = []
    rets = [math.sin(i)*.01 for i in range(1000)]
    def fake(train, observed, **kwargs):
        seen.append((list(train), list(observed)))
        return {h: [-.02, 0., .02] for h in (5,10,20)}
    monkeypatch.setattr(p, 'TORCH_OK', True)
    monkeypatch.setattr(p, 'direct_lstm_samples', fake)
    result = p.validate(rets, n_folds=3, evaluate_lstm=True)
    for fold, (train, observed) in zip(result['folds'], seen):
        cut = fold['cut_index']
        assert train == rets[:cut-p.PURGE]
        assert observed == rets[:cut]
    assert result['lstm']['direct_horizon_output'] is True
    assert result['promotion']['lstm']['passed'] is False
    assert result['verdict']['primary_model'] != 'lstm'
