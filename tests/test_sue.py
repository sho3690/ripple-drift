import numpy as np
import pandas as pd

from app.models import sue


def _trading_days():
    return pd.bdate_range("2024-01-01", "2024-12-31")


def test_event_day0_intraday_and_after_close():
    td = _trading_days()
    d = pd.Timestamp("2024-03-06")  # Wednesday
    assert sue.event_day0(d, False, td) == d
    assert sue.event_day0(d, True, td) == pd.Timestamp("2024-03-07")
    # 週末発表 → 月曜
    sat = pd.Timestamp("2024-03-09")
    assert sue.event_day0(sat, False, td) == pd.Timestamp("2024-03-11")


def test_sue_analyst_scaled_by_price():
    ev = pd.DataFrame({"eps_estimate": [10.0, 10.0], "eps_actual": [12.0, 8.0], "price_prev": [1000.0, 0.0]})
    out = sue.sue_analyst(ev)
    assert abs(out.iloc[0] - 0.002) < 1e-12
    assert np.isnan(out.iloc[1])


def test_sue_srw_detects_seasonal_jump():
    # 12 四半期。季節差分が 1 で安定 → 最後だけ +5 の跳ね
    eps = [10 + i for i in range(12)]
    eps[-1] += 5
    ev = pd.DataFrame({"symbol": ["A"] * 12, "date": pd.bdate_range("2021-01-01", periods=12, freq="QS"), "eps_actual": eps})
    out = sue.sue_seasonal_random_walk(ev, window=8)
    assert np.isnan(out.iloc[3])            # 最初の 4 期は季節差分不可
    assert out.iloc[-1] > 3                 # 跳ねが強い正の SUE として出る


def test_cross_sectional_rank_and_deciles():
    vals = pd.Series(np.arange(20, dtype=float))
    groups = pd.Series(["Q1"] * 20)
    r = sue.cross_sectional_rank(vals, groups)
    assert r.iloc[0] == 0.05 and r.iloc[-1] == 1.0
    d = sue.to_decile(r)
    assert d.min() == 1 and d.max() == 10
    small = sue.cross_sectional_rank(pd.Series([1.0, 2.0]), pd.Series(["Q", "Q"]))
    assert small.isna().all()


def test_event_day0_before_price_history_is_none():
    td = _trading_days()  # 2024 年のみ
    assert sue.event_day0(pd.Timestamp("2015-04-28"), False, td) is None
    assert sue.event_day0(pd.Timestamp("2023-12-29"), False, td) == pd.Timestamp("2024-01-01")
