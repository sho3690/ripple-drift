"""PEAD（Post-Earnings-Announcement Drift）イベントスタディ。

  市場調整累積異常リターン:
    CAR_i[a, b] = Σ_{τ=a}^{b} (r_i,τ − r_m,τ)        （τ は day 0 からの営業日オフセット）
  既定窓は [+2, +61)（=60 営業日）。発表当日〜翌日の即時反応は day 0..1 として別に集計する。

  条件別ドリフト表:
    SUE 三分位 × SUE.txt 三分位 の 3×3 セルで CAR60 の平均・勝率・件数。
    「最も強いドリフトが出る条件」= n ≥ MIN_CELL_N のセルのうち平均 CAR60 最大。

  横断面回帰（HC1 頑健標準誤差）:
    CAR60_i = a + b·SUE_rank_i + c·SUE.txt_i + d·(SUE_rank_i × SUE.txt_i) + e_i
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config


MAX_DAILY_ABS = 0.6     # 60 営業日窓の中で 1 日 60% 超の市場調整リターンはデータ不良とみなす
CAR_WINSOR = 0.6        # CAR60 の打ち切り幅（±60%）


def compute_car(ev: pd.DataFrame, adj_returns: pd.DataFrame,
                start: int = config.CAR_START_OFFSET, horizon: int = config.CAR_HORIZON,
                short_h: int = config.CAR_SHORT_HORIZON) -> pd.DataFrame:
    """各イベントに CAR 経路と要約値を付与する。"""
    ev = ev.copy()
    paths, car_h, car_s, react, complete, days_elapsed = [], [], [], [], [], []
    series_cache: dict[str, pd.Series] = {}
    for _, r in ev.iterrows():
        sym, d0 = r["symbol"], r["day0"]
        if sym not in adj_returns.columns or pd.isna(d0):
            paths.append(None); car_h.append(np.nan); car_s.append(np.nan); react.append(np.nan); complete.append(False); days_elapsed.append(np.nan)
            continue
        s = series_cache.get(sym)
        if s is None:
            s = adj_returns[sym].dropna()
            series_cache[sym] = s
        if d0 not in s.index:
            paths.append(None); car_h.append(np.nan); car_s.append(np.nan); react.append(np.nan); complete.append(False); days_elapsed.append(np.nan)
            continue
        pos = s.index.get_loc(d0)
        window = s.iloc[pos + start: pos + start + horizon]
        # データ不良対策: 窓内に |日次リターン| > MAX_DAILY_ABS があれば無効（未調整の分割・誤データ）
        if (window.abs() > MAX_DAILY_ABS).any() or (s.iloc[max(0, pos - 1): pos + 2].abs() > MAX_DAILY_ABS).any():
            paths.append(None); car_h.append(np.nan); car_s.append(np.nan); react.append(np.nan); complete.append(False)
            days_elapsed.append(int(len(s) - pos - 1))
            continue
        path = np.cumsum(window.to_numpy())
        n = len(path)
        paths.append([round(float(x), 5) for x in path])
        car_h.append(float(np.clip(path[-1], -CAR_WINSOR, CAR_WINSOR)) if n >= horizon else np.nan)
        car_s.append(float(np.clip(path[short_h - 1], -CAR_WINSOR, CAR_WINSOR)) if n >= short_h else np.nan)
        react.append(float(s.iloc[pos: pos + 2].sum()) if pos + 2 <= len(s) else np.nan)
        complete.append(n >= horizon)
        days_elapsed.append(int(len(s) - pos - 1))
    ev["car_path"] = paths
    ev[f"car{horizon}"] = car_h
    ev[f"car{short_h}"] = car_s
    ev["reaction_0_1"] = react
    ev["car_complete"] = complete
    ev["days_since_day0"] = days_elapsed
    return ev


def mean_path_by_group(ev: pd.DataFrame, group_col: str, horizon: int = config.CAR_HORIZON) -> dict:
    out = {}
    for g, sub in ev.groupby(group_col):
        if pd.isna(g):
            continue
        mats = [p for p in sub["car_path"] if p is not None and len(p) >= horizon]
        if len(mats) < 5:
            continue
        M = np.array([p[:horizon] for p in mats])
        out[str(int(g)) if isinstance(g, (int, float, np.integer, np.floating)) else str(g)] = {
            "mean": [round(float(x), 5) for x in M.mean(axis=0)],
            "n": int(M.shape[0]),
        }
    return out


def conditional_table(ev: pd.DataFrame, sue_col: str = "sue_tercile", txt_col: str = "txt_tercile",
                      car_col: str = f"car{config.CAR_HORIZON}") -> list[dict]:
    rows = []
    sub = ev.dropna(subset=[sue_col, car_col])
    has_txt = sub[txt_col].notna()
    for s in (1, 2, 3):
        for t in (None, 1, 2, 3):
            if t is None:
                cell = sub[(sub[sue_col] == s)]
            else:
                cell = sub[(sub[sue_col] == s) & (sub[txt_col] == t)]
            n = int(len(cell))
            car = cell[car_col]
            rows.append({
                "sue_tercile": s, "txt_tercile": t, "n": n,
                "mean_car": None if n == 0 else round(float(car.mean()), 5),
                "median_car": None if n == 0 else round(float(car.median()), 5),
                "hit_rate": None if n == 0 else round(float((car > 0).mean()), 4),
                "t": None if n < 3 else round(float(car.mean() / (car.std(ddof=1) / np.sqrt(n))), 2),
            })
    _ = has_txt
    return rows


def best_condition(table: list[dict], min_n: int = config.MIN_CELL_N) -> dict | None:
    """最もドリフトが強い条件。小サンプルの外れ値に引きずられないよう、
    n ≥ min_n かつ t ≥ 1.5 かつ勝率 ≥ 0.5 を満たすセルの中で平均 CAR 最大を選ぶ。
    テキスト条件付きセルで該当が無ければ、テキスト無し（全体）の行から選ぶ。"""
    def ok(r):
        return r["n"] >= min_n and r["mean_car"] is not None and (r["t"] or 0) >= 1.5 and (r["hit_rate"] or 0) >= 0.5
    with_txt = [r for r in table if r["txt_tercile"] is not None and ok(r)]
    if with_txt:
        with_txt.sort(key=lambda r: (r["mean_car"], r["n"]), reverse=True)
        return with_txt[0]
    overall = [r for r in table if r["txt_tercile"] is None and ok(r)]
    if overall:
        overall.sort(key=lambda r: (r["mean_car"], r["n"]), reverse=True)
        return overall[0]
    cand = [r for r in table if r["n"] >= min_n and r["mean_car"] is not None]
    if not cand:
        return None
    cand.sort(key=lambda r: (r["mean_car"], r["n"]), reverse=True)
    return cand[0]


def drift_regression(ev: pd.DataFrame, car_col: str = f"car{config.CAR_HORIZON}") -> dict:
    """CAR ~ SUE_rank + SUE.txt + 交互作用。HC1 頑健 t 値。"""
    import statsmodels.api as sm

    sub = ev.dropna(subset=[car_col, "sue_rank"]).copy()
    if len(sub) < 30:
        return {"n": int(len(sub))}
    sub["sue_c"] = sub["sue_rank"] - 0.5
    X = pd.DataFrame({"const": 1.0, "sue_c": sub["sue_c"]})
    if sub["sue_txt"].notna().sum() >= 30:
        sub_t = sub.dropna(subset=["sue_txt"]).copy()
        X = pd.DataFrame({"const": 1.0, "sue_c": sub_t["sue_c"], "sue_txt": sub_t["sue_txt"],
                          "sue_c_x_txt": sub_t["sue_c"] * sub_t["sue_txt"]})
        y = sub_t[car_col]
    else:
        y = sub[car_col]
    try:
        res = sm.OLS(y.to_numpy(), X.to_numpy()).fit(cov_type="HC1")
    except Exception:
        return {"n": int(len(y))}
    out = {"n": int(len(y)), "r2": round(float(res.rsquared), 4), "coef": {}}
    for name, b, t in zip(X.columns, res.params, res.tvalues):
        out["coef"][name] = {"b": round(float(b), 5), "t": round(float(t), 2)}
    return out


def long_short_by_decile(ev: pd.DataFrame, car_col: str = f"car{config.CAR_HORIZON}") -> dict:
    sub = ev.dropna(subset=["sue_decile", car_col])
    if sub.empty:
        return {}
    by = sub.groupby("sue_decile")[car_col].agg(["mean", "count", "std"])
    out = {int(k): {"mean": round(float(v["mean"]), 5), "n": int(v["count"]),
                    "t": round(float(v["mean"] / (v["std"] / np.sqrt(v["count"]))), 2) if v["count"] > 2 and v["std"] > 0 else None}
           for k, v in by.iterrows()}
    if 10 in out and 1 in out:
        top, bot = sub[sub["sue_decile"] == 10][car_col], sub[sub["sue_decile"] == 1][car_col]
        diff = float(top.mean() - bot.mean())
        se = float(np.sqrt(top.var(ddof=1) / len(top) + bot.var(ddof=1) / len(bot)))
        out["long_short"] = {"mean": round(diff, 5), "t": round(diff / se, 2) if se > 0 else None,
                             "n_top": int(len(top)), "n_bottom": int(len(bot))}
    return out


def customer_surprise_spillover(ev: pd.DataFrame, weights: dict[str, dict[str, float]],
                                lookback_days: int = 90, car_col: str = f"car{config.CAR_HORIZON}") -> dict:
    """サプライヤーのイベント直前 lookback 日以内に出た顧客 SUE の加重平均が、
    サプライヤーの CAR を予測するかを検証する（Cohen–Frazzini の「顧客ショック波及」版）。"""
    import statsmodels.api as sm

    by_sym = {s: g.sort_values("date") for s, g in ev.groupby("symbol")}
    rows = []
    for sup, wmap in weights.items():
        if sup not in by_sym:
            continue
        for _, r in by_sym[sup].iterrows():
            if pd.isna(r.get(car_col)) or pd.isna(r.get("sue_rank")):
                continue
            num = den = 0.0
            for cust, w in wmap.items():
                g = by_sym.get(cust)
                if g is None:
                    continue
                prior = g[(g["date"] < r["date"]) & (g["date"] >= r["date"] - pd.Timedelta(days=lookback_days))]
                prior = prior.dropna(subset=["sue_rank"])
                if prior.empty:
                    continue
                num += w * float(prior.iloc[-1]["sue_rank"] - 0.5)
                den += w
            if den > 0:
                rows.append((float(r[car_col]), num / den, float(r["sue_rank"]) - 0.5))
    if len(rows) < 20:
        return {"n": len(rows)}
    A = np.array(rows)
    X = np.column_stack([np.ones(len(A)), A[:, 1], A[:, 2]])
    try:
        res = sm.OLS(A[:, 0], X).fit(cov_type="HC1")
    except Exception:
        return {"n": len(rows)}
    return {
        "n": int(len(rows)),
        "beta_customer_sue": {"b": round(float(res.params[1]), 5), "t": round(float(res.tvalues[1]), 2)},
        "beta_own_sue": {"b": round(float(res.params[2]), 5), "t": round(float(res.tvalues[2]), 2)},
        "r2": round(float(res.rsquared), 4),
    }


def add_pre_event_vol(ev: pd.DataFrame, daily: pd.DataFrame, window: int = 60) -> pd.DataFrame:
    """イベント直前 window 営業日の年率ボラ（Mendenhall 2004: 裁定リスクが高いほどドリフトが大きい）。"""
    ev = ev.copy()
    out = np.full(len(ev), np.nan)
    cache: dict[str, pd.Series] = {}
    for i, (idx, r) in enumerate(ev.iterrows()):
        sym, d0 = r["symbol"], r["day0"]
        if sym not in daily.columns or pd.isna(d0):
            continue
        s = cache.get(sym)
        if s is None:
            s = daily[sym].dropna(); cache[sym] = s
        if d0 not in s.index:
            continue
        pos = s.index.get_loc(d0)
        if pos < window // 2:
            continue
        w = s.iloc[max(0, pos - window): pos]
        out[i] = float(w.std(ddof=1) * np.sqrt(250))
    ev["vol_pre"] = out
    q = ev["vol_pre"].dropna()
    if len(q) >= 30:
        t1, t2 = q.quantile(1 / 3), q.quantile(2 / 3)
        ev["vol_tercile"] = np.where(ev["vol_pre"].isna(), np.nan, np.where(ev["vol_pre"] < t1, 1, np.where(ev["vol_pre"] < t2, 2, 3)))
        ev.attrs["vol_terciles"] = [float(t1), float(t2)]
    else:
        ev["vol_tercile"] = np.nan
        ev.attrs["vol_terciles"] = [None, None]
    return ev


def decile_vol_table(ev: pd.DataFrame, car_col: str = f"car{config.CAR_HORIZON}") -> list[dict]:
    """SUE 十分位 × ボラ三分位 の平均 CAR60・勝率・n・t。"""
    rows = []
    sub = ev.dropna(subset=["sue_decile", car_col])
    for d in range(1, 11):
        for v in (None, 1, 2, 3):
            cell = sub[sub["sue_decile"] == d] if v is None else sub[(sub["sue_decile"] == d) & (sub["vol_tercile"] == v)]
            n = int(len(cell)); car = cell[car_col]
            rows.append({"sue_decile": d, "vol_tercile": v, "n": n,
                         "mean_car": None if n == 0 else round(float(car.mean()), 5),
                         "hit_rate": None if n == 0 else round(float((car > 0).mean()), 4),
                         "t": None if n < 3 or car.std(ddof=1) == 0 else round(float(car.mean() / (car.std(ddof=1) / np.sqrt(n))), 2)})
    return rows
