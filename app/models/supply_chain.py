"""サプライチェーン・モメンタム（Cohen & Frazzini 2008）。

  G = (V, E): supplier → customer の有向グラフ。エッジ重み w_ij は顧客 j がサプライヤー i の
  売上に占める比率（サプライヤーごとに正規化して顧客ポートフォリオ重みとする）。

  顧客モメンタム: CM_i,t = Σ_j w̃_ij · r̃_j,t   （r̃ は市場調整済み期間リターン）

  リード・ラグ構造: 交差自己相関 ρ_ij(k) = corr(r_i,t, r_j,t−k)、k = 0..K
  → エッジ加重平均 ρ̄(k) の k ≥ 1 における最大点 k* を「波及ラグ」とする。

  予測回帰（Fama–MacBeth 1973）:
    r_i,t+1 = α_t + β_t CM_i,t + γ_t r_i,t + δ_t MOM_i,t + ε_i,t+1
    β̂ = mean_t(β_t)、t 値は Newey–West（Bartlett カーネル）で系列相関を補正。

  過小反応ギャップ: gap_i = z(CM_i) − z(r̃_i)。顧客は動いたが本人がまだ動いていない度合い。
"""
from __future__ import annotations

from dataclasses import dataclass

import networkx as nx
import numpy as np
import pandas as pd

from .. import config
from ..universe import Edge, Node


def build_graph(nodes: list[Node], edges: list[Edge], available: set[str] | None = None) -> nx.DiGraph:
    G = nx.DiGraph()
    for n in nodes:
        if available is not None and n.symbol not in available:
            continue
        G.add_node(n.symbol, name=n.name, market=n.market, sector=n.sector, screened=n.screened)
    for e in edges:
        if e.supplier in G and e.customer in G:
            G.add_edge(e.supplier, e.customer, weight=float(e.weight), source=e.source, note=e.note)
    return G


def customer_weights(G: nx.DiGraph) -> dict[str, dict[str, float]]:
    """サプライヤーごとの正規化顧客重み w̃_ij。"""
    out: dict[str, dict[str, float]] = {}
    for s in G.nodes:
        succ = list(G.successors(s))
        if not succ:
            continue
        tot = sum(G[s][c]["weight"] for c in succ)
        if tot <= 0:
            continue
        out[s] = {c: G[s][c]["weight"] / tot for c in succ}
    return out


def customer_momentum(period_returns: pd.DataFrame, weights: dict[str, dict[str, float]]) -> pd.DataFrame:
    """期間 × サプライヤーの CM。顧客の欠損は重みを再正規化して除外する。"""
    cols = {}
    for s, wmap in weights.items():
        custs = [c for c in wmap if c in period_returns.columns]
        if not custs:
            continue
        R = period_returns[custs]
        W = pd.Series({c: wmap[c] for c in custs})
        mask = R.notna()
        num = (R.fillna(0.0) * W).sum(axis=1)
        den = (mask * W).sum(axis=1)
        cm = num / den.replace(0.0, np.nan)
        cols[s] = cm
    return pd.DataFrame(cols)


@dataclass
class LeadLagResult:
    lags: list[int]
    mean_corr: list[float]
    weighted_corr: list[float]
    n_edges: list[int]
    best_lag: int
    best_corr: float
    frequency: str

    def as_dict(self) -> dict:
        return {
            "frequency": self.frequency, "lags": self.lags,
            "mean_corr": [round(x, 4) for x in self.mean_corr],
            "weighted_corr": [round(x, 4) for x in self.weighted_corr],
            "n_edges": self.n_edges, "best_lag": self.best_lag, "best_corr": round(self.best_corr, 4),
        }


def cross_autocorrelation(period_returns: pd.DataFrame, G: nx.DiGraph, max_lag: int,
                          frequency: str, min_obs: int = 24) -> LeadLagResult:
    """ρ_ij(k) = corr(r_supplier,t, r_customer,t−k) をエッジごとに計算し集約する。"""
    lags = list(range(0, max_lag + 1))
    sums = np.zeros(len(lags)); wsums = np.zeros(len(lags)); wtot = np.zeros(len(lags)); counts = np.zeros(len(lags), dtype=int)
    for s, c, d in G.edges(data=True):
        if s not in period_returns.columns or c not in period_returns.columns:
            continue
        rs, rc = period_returns[s], period_returns[c]
        w = float(d.get("weight", 0.1))
        for i, k in enumerate(lags):
            pair = pd.concat([rs, rc.shift(k)], axis=1).dropna()
            if len(pair) < min_obs:
                continue
            rho = float(pair.iloc[:, 0].corr(pair.iloc[:, 1]))
            if np.isnan(rho):
                continue
            sums[i] += rho; wsums[i] += w * rho; wtot[i] += w; counts[i] += 1
    mean_corr = [float(sums[i] / counts[i]) if counts[i] else float("nan") for i in range(len(lags))]
    wcorr = [float(wsums[i] / wtot[i]) if wtot[i] > 0 else float("nan") for i in range(len(lags))]
    cand = [(wcorr[i], k) for i, k in enumerate(lags) if k >= 1 and not np.isnan(wcorr[i])]
    if cand:
        best_corr, best_lag = max(cand)
    else:
        best_corr, best_lag = float("nan"), 1
    return LeadLagResult(lags, mean_corr, wcorr, counts.tolist(), int(best_lag), float(best_corr), frequency)


def newey_west_tstat(series: np.ndarray, lag: int | None = None) -> tuple[float, float, float]:
    """平均・NW 標準誤差・t 値。Bartlett カーネル。"""
    x = np.asarray(series, dtype=float)
    x = x[~np.isnan(x)]
    T = len(x)
    if T < 3:
        return float("nan"), float("nan"), float("nan")
    if lag is None:
        lag = int(np.floor(4 * (T / 100.0) ** (2.0 / 9.0)))
    mu = x.mean()
    e = x - mu
    var = float(e @ e) / T
    for l in range(1, lag + 1):
        gamma = float(e[l:] @ e[:-l]) / T
        var += 2.0 * (1.0 - l / (lag + 1.0)) * gamma
    se = np.sqrt(max(var, 1e-18) / T)
    return float(mu), float(se), float(mu / se)


def own_momentum(period_returns: pd.DataFrame, lookback: int = config.MOM_LOOKBACK_MONTHS,
                 skip: int = config.MOM_SKIP_MONTHS) -> pd.DataFrame:
    """MOM_i,t = 累積リターン(t−skip−lookback+1 .. t−skip)。標準的な 12-2 モメンタム。"""
    logr = np.log1p(period_returns.clip(lower=-0.95))
    return np.expm1(logr.shift(skip).rolling(lookback, min_periods=max(6, lookback // 2)).sum())


def fama_macbeth(period_returns: pd.DataFrame, cm: pd.DataFrame, mom: pd.DataFrame,
                 min_cs: int = 8) -> dict:
    """横断面回帰を各期で行い、係数の時系列平均と NW t 値を返す。"""
    suppliers = [s for s in cm.columns if s in period_returns.columns]
    betas, gammas, deltas, alphas, ns = [], [], [], [], []
    dates = []
    idx = period_returns.index
    for i in range(len(idx) - 1):
        t, t1 = idx[i], idx[i + 1]
        rows = []
        for s in suppliers:
            y = period_returns.at[t1, s] if s in period_returns.columns else np.nan
            x1 = cm.at[t, s] if t in cm.index else np.nan
            x2 = period_returns.at[t, s]
            x3 = mom.at[t, s] if (t in mom.index and s in mom.columns) else np.nan
            if any(np.isnan(v) for v in (y, x1, x2, x3)):
                continue
            rows.append((y, x1, x2, x3))
        if len(rows) < min_cs:
            continue
        A = np.array(rows)
        y = A[:, 0]
        X = np.column_stack([np.ones(len(A)), A[:, 1], A[:, 2], A[:, 3]])
        try:
            b, *_ = np.linalg.lstsq(X, y, rcond=None)
        except np.linalg.LinAlgError:
            continue
        alphas.append(b[0]); betas.append(b[1]); gammas.append(b[2]); deltas.append(b[3]); ns.append(len(rows)); dates.append(t1)
    if not betas:
        return {"n_periods": 0}
    out = {"n_periods": len(betas), "avg_cross_section": float(np.mean(ns))}
    for name, arr in (("beta_cm", betas), ("gamma_own", gammas), ("delta_mom", deltas), ("alpha", alphas)):
        mu, se, t = newey_west_tstat(np.array(arr))
        out[name] = {"mean": round(mu, 5), "se": round(se, 5), "t": round(t, 2)}
    out["beta_series"] = [[d.strftime("%Y-%m"), round(float(b), 5)] for d, b in zip(dates, betas)]
    return out


def customer_momentum_sort(period_returns: pd.DataFrame, cm: pd.DataFrame, n_groups: int = 5) -> dict:
    """CM で毎期 n 分位に並べ、翌期の等加重リターンを集計（Cohen–Frazzini Table II 型）。"""
    idx = period_returns.index
    group_rets: dict[int, list[float]] = {g: [] for g in range(1, n_groups + 1)}
    for i in range(len(idx) - 1):
        t, t1 = idx[i], idx[i + 1]
        if t not in cm.index:
            continue
        x = cm.loc[t].dropna()
        x = x[[s for s in x.index if s in period_returns.columns]]
        if len(x) < n_groups * 2:
            continue
        y = period_returns.loc[t1, x.index]
        ok = y.notna()
        x, y = x[ok], y[ok]
        if len(x) < n_groups * 2:
            continue
        ranks = x.rank(pct=True, method="first")
        g = np.ceil(ranks * n_groups).clip(1, n_groups).astype(int)
        for k in range(1, n_groups + 1):
            sel = y[g == k]
            if len(sel):
                group_rets[k].append(float(sel.mean()))
    summary = {}
    for k, arr in group_rets.items():
        if arr:
            mu, se, t = newey_west_tstat(np.array(arr))
            summary[k] = {"mean": round(mu, 5), "t": round(t, 2), "n": len(arr)}
    if summary and n_groups in summary and 1 in summary:
        top, bot = group_rets[n_groups], group_rets[1]
        m = min(len(top), len(bot))
        ls = np.array(top[-m:]) - np.array(bot[-m:])
        mu, se, t = newey_west_tstat(ls)
        summary["long_short"] = {"mean": round(mu, 5), "t": round(t, 2), "n": int(m)}
    return summary


def zscore(s: pd.Series) -> pd.Series:
    s = s.astype(float)
    sd = s.std(ddof=1)
    if not np.isfinite(sd) or sd <= 1e-12:
        return s * 0.0
    return (s - s.mean()) / sd


def underreaction_gap(cm_now: pd.Series, own_now: pd.Series) -> pd.Series:
    """gap_i = z(CM_i) − z(r̃_i)。両方揃う銘柄のみ。"""
    both = cm_now.index.intersection(own_now.index)
    cm_z = zscore(cm_now.loc[both].dropna())
    own_z = zscore(own_now.loc[cm_z.index])
    return (cm_z - own_z).dropna()


def graph_payload(G: nx.DiGraph, highlight: set[str] | None = None) -> dict:
    highlight = highlight or set()
    nodes, links = [], []
    for n, d in G.nodes(data=True):
        deg_in = G.in_degree(n)
        nodes.append({
            "id": n, "name": d.get("name", n), "market": d.get("market"), "sector": d.get("sector"),
            "screened": bool(d.get("screened", False)), "customers": G.out_degree(n), "suppliers": deg_in,
            "highlight": n in highlight,
        })
    for s, c, d in G.edges(data=True):
        links.append({"source": s, "target": c, "weight": round(float(d.get("weight", 0)), 3),
                      "provenance": d.get("source", ""), "note": d.get("note", "")})
    return {"nodes": nodes, "links": links}
