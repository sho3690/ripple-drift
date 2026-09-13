import numpy as np
import pandas as pd

from app.models import supply_chain as sc
from app.universe import Edge, Node


def _graph():
    nodes = [Node("S1", "s1", "JP", "x"), Node("S2", "s2", "JP", "x"), Node("C", "c", "US", "y", False)]
    edges = [Edge("S1", "C", 0.6, "edinet"), Edge("S2", "C", 0.3, "public")]
    return sc.build_graph(nodes, edges)


def test_customer_momentum_renormalizes_missing():
    G = _graph()
    w = sc.customer_weights(G)
    assert w["S1"] == {"C": 1.0}
    idx = pd.date_range("2024-01-31", periods=3, freq="ME")
    R = pd.DataFrame({"C": [0.1, np.nan, -0.05], "S1": [0, 0, 0], "S2": [0, 0, 0]}, index=idx)
    cm = sc.customer_momentum(R, w)
    assert abs(cm.loc[idx[0], "S1"] - 0.1) < 1e-12
    assert np.isnan(cm.loc[idx[1], "S1"])


def test_cross_autocorrelation_finds_lag():
    rng = np.random.default_rng(1)
    T = 240
    c = rng.normal(size=T)
    # サプライヤーは顧客の 2 期前に強く連動
    s1 = np.r_[0, 0, c[:-2]] * 0.8 + rng.normal(scale=0.3, size=T)
    idx = pd.date_range("2000-01-31", periods=T, freq="ME")
    R = pd.DataFrame({"C": c, "S1": s1, "S2": rng.normal(size=T)}, index=idx)
    res = sc.cross_autocorrelation(R, _graph(), max_lag=6, frequency="monthly")
    assert res.best_lag == 2
    assert res.weighted_corr[2] > 0.5


def test_newey_west_tstat_reasonable():
    rng = np.random.default_rng(2)
    x = rng.normal(loc=0.5, scale=1.0, size=400)
    mu, se, t = sc.newey_west_tstat(x)
    assert 0.3 < mu < 0.7 and t > 5


def test_underreaction_gap_sign():
    cm = pd.Series({"A": 0.10, "B": 0.00, "C": -0.10})
    own = pd.Series({"A": -0.05, "B": 0.00, "C": 0.05})
    gap = sc.underreaction_gap(cm, own)
    assert gap["A"] > gap["B"] > gap["C"]
