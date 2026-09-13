import numpy as np
import pandas as pd

from app.models import pead


def test_compute_car_window_and_completeness():
    idx = pd.bdate_range("2024-01-01", periods=100)
    r = pd.Series(0.01, index=idx)
    adj = pd.DataFrame({"A": r})
    ev = pd.DataFrame({"symbol": ["A", "A"], "day0": [idx[10], idx[90]]})
    out = pead.compute_car(ev, adj, start=2, horizon=60, short_h=20)
    assert out.loc[0, "car_complete"] and abs(out.loc[0, "car60"] - 0.6) < 1e-9
    assert abs(out.loc[0, "car20"] - 0.2) < 1e-9
    assert abs(out.loc[0, "reaction_0_1"] - 0.02) < 1e-9
    assert not out.loc[1, "car_complete"] and np.isnan(out.loc[1, "car60"])
    assert len(out.loc[1, "car_path"]) == 100 - 90 - 2


def test_conditional_table_and_best():
    ev = pd.DataFrame({
        "sue_tercile": [3] * 30 + [1] * 30,
        "txt_tercile": [3] * 30 + [1] * 30,
        "car60": [0.05] * 30 + [-0.02] * 30,
    })
    tbl = pead.conditional_table(ev)
    best = pead.best_condition(tbl, min_n=20)
    assert best["sue_tercile"] == 3 and best["txt_tercile"] == 3 and best["n"] == 30
