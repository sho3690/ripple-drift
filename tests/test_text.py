import numpy as np

from app.models import text


def test_japanese_positive_confident_text_scores_high():
    t = "当第2四半期は増収増益で過去最高益を更新しました。受注も好調で、通期計画は確実に達成できる見込みです。"
    f = text.lexicon_features(t)
    assert f.language == "ja"
    assert f.tone > 0.5 and f.certainty > 0
    s = text.score_text(t, backend="lexicon")
    assert s["sue_txt"] > 0.3
    assert s["confidence_score"] > 65


def test_japanese_negative_uncertain_text_scores_low():
    t = "減収減益となり、下方修正を行いました。先行きは不透明で、需要動向を慎重に注視する必要があります。"
    s = text.score_text(t, backend="lexicon")
    assert s["sue_txt"] < -0.3
    assert s["features"]["tone"] < 0


def test_english_fallback_lexicon():
    t = "We delivered record revenue and strong growth, exceeding guidance. We are confident and on track for the full year."
    f = text.lexicon_features(t)
    assert f.language == "en"
    assert f.tone > 0.5


def test_logit_fit_learns_sign():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(200, 5))
    y = (X[:, 0] * 2.0 + rng.normal(scale=0.5, size=200) > 0).astype(float)
    m = text.TextSurpriseModel().fit(X, y)
    assert m.fitted and m.coef[0] > 0.5
    assert m.sue_txt(np.array([2.0, 0, 0, 0, 0])) > 0.8
    assert m.sue_txt(np.array([-2.0, 0, 0, 0, 0])) < -0.8
    m2 = text.TextSurpriseModel.from_dict(m.to_dict())
    assert np.allclose(m2.coef, m.coef)
