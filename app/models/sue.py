"""SUE（Standardized Unexpected Earnings, 標準化予想外利益）。

二系統を実装する。

1. アナリスト基準（Meursault et al. 2023 と同型）
   SUE_i,q = (EPS_actual − EPS_consensus) / P_{i, t0−1}
   価格でスケーリングすることで、EPS 水準の銘柄間差を吸収する。

2. 季節ランダムウォーク（Seasonal Random Walk with drift; Foster, Olsen & Shevlin 1984）
   ΔEPS_q = EPS_q − EPS_{q−4}
   SUE^SRW_q = (ΔEPS_q − mean(ΔEPS_{q−1..q−W})) / std(ΔEPS_{q−1..q−W})
   コンセンサスが無い銘柄・期のフォールバックとして用いる。

いずれも横断面（同一四半期の発表群）でランク標準化し、十分位（decile）へ落とす。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config


def event_day0(date: pd.Timestamp, after_close: bool, trading_days: pd.DatetimeIndex) -> pd.Timestamp | None:
    """市場が最初に反応できる営業日（day 0）。

    発表が取引時間中なら当日、引け後なら翌営業日。発表日が休日ならその後の最初の営業日。
    """
    if trading_days.empty:
        return None
    pos = trading_days.searchsorted(date, side="left")
    if pos >= len(trading_days):
        return None
    d0 = trading_days[pos]
    if (d0 - date).days > 10:          # 価格履歴より前の発表 → 対応する営業日が無い
        return None
    if d0 == date and after_close:
        if pos + 1 >= len(trading_days):
            return None
        d0 = trading_days[pos + 1]
    return d0


def attach_day0_and_price(events: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """各イベントに day0・直前終値（P_{t0−1}）を付与する（銘柄ごとに系列をキャッシュ）。"""
    ev = events.copy()
    day0 = pd.Series(pd.NaT, index=ev.index, dtype="datetime64[ns]")
    prev_px = pd.Series(np.nan, index=ev.index, dtype=float)
    for sym, g in ev.groupby("symbol"):
        if sym not in prices.columns:
            continue
        s = prices[sym].dropna()
        if s.empty:
            continue
        idx = s.index
        vals = s.to_numpy()
        for i, r in g.iterrows():
            d0 = event_day0(pd.Timestamp(r["date"]), bool(r["after_close"]), idx)
            if d0 is None:
                continue
            pos = idx.get_loc(d0)
            day0.at[i] = d0
            if pos > 0:
                prev_px.at[i] = float(vals[pos - 1])
    ev["day0"] = day0
    ev["price_prev"] = prev_px
    return ev


def sue_analyst(ev: pd.DataFrame) -> pd.Series:
    """(actual − estimate) / P_{t0−1}。百分率ではなく比率のまま返す。"""
    est = pd.to_numeric(ev["eps_estimate"], errors="coerce")
    act = pd.to_numeric(ev["eps_actual"], errors="coerce")
    px = pd.to_numeric(ev["price_prev"], errors="coerce")
    out = (act - est) / px
    out[(px <= 0) | px.isna()] = np.nan
    return out


def sue_seasonal_random_walk(ev: pd.DataFrame, window: int = config.SRW_WINDOW) -> pd.Series:
    """銘柄ごとに時系列順で季節差分を取り、過去 window 期の平均・標準偏差で標準化。"""
    out = pd.Series(np.nan, index=ev.index, dtype=float)
    for sym, g in ev.groupby("symbol"):
        g = g.sort_values("date")
        eps = pd.to_numeric(g["eps_actual"], errors="coerce").to_numpy()
        n = len(eps)
        vals = np.full(n, np.nan)
        for q in range(4, n):
            d_now = eps[q] - eps[q - 4]
            hist = [eps[k] - eps[k - 4] for k in range(max(4, q - window), q)]
            hist = [h for h in hist if not np.isnan(h)]
            if np.isnan(d_now) or len(hist) < 4:
                continue
            mu, sd = float(np.mean(hist)), float(np.std(hist, ddof=1))
            # 分散の下限: 季節差分が安定しすぎている場合に SUE が発散するのを防ぐ
            sd = max(sd, 0.1 * float(np.mean(np.abs(hist))), 1e-9)
            vals[q] = (d_now - mu) / sd
        out.loc[g.index] = vals
    return out


def quarter_bucket(dates: pd.Series) -> pd.Series:
    d = pd.to_datetime(dates)
    return d.dt.year.astype(str) + "Q" + d.dt.quarter.astype(str)


def cross_sectional_rank(values: pd.Series, groups: pd.Series, min_group: int = 8) -> pd.Series:
    """グループ内パーセンタイルランク（0..1）。グループが小さすぎれば NaN。"""
    out = pd.Series(np.nan, index=values.index, dtype=float)
    for g, idx in groups.groupby(groups).groups.items():
        v = values.loc[idx].dropna()
        if len(v) < min_group:
            continue
        out.loc[v.index] = v.rank(pct=True, method="average")
    return out


def to_decile(pct_rank: pd.Series) -> pd.Series:
    d = np.ceil(pct_rank * 10)
    return d.clip(lower=1, upper=10)


def to_tercile(pct_rank: pd.Series) -> pd.Series:
    t = np.ceil(pct_rank * 3)
    return t.clip(lower=1, upper=3)


def combine_sue(ev: pd.DataFrame) -> pd.Series:
    """主系（アナリスト）を優先し、無ければ SRW を使う統合 SUE。

    2 系統はスケールが異なるため、各系統を四半期横断面ランクへ落としてから統合する。
    """
    q = quarter_bucket(ev["date"])
    r_an = cross_sectional_rank(ev["sue_analyst"], q)
    r_srw = cross_sectional_rank(ev["sue_srw"], q)
    combined = r_an.where(r_an.notna(), r_srw)
    return combined
