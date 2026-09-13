"""合成スクリーン: 3 リング・スコアと上位 5% 抽出、初心者向け説明文の生成。

Ring A「決算サプライズ」: 直近イベントの統合 SUE 横断面ランクを、鮮度減衰付きで 0..100 に写像。
    A = 50 + (rank − 0.5)·100·max(0, 1 − days/SIGNAL_DECAY_DAYS)
Ring B「経営陣の自信」: SUE.txt ∈ [−1,1] → B = 50 + 50·SUE.txt（テキスト無しは 50・フラグ）
Ring C「取引先の追い風」: C = 100·(0.6·pct(CM) + 0.4·pct(gap))
    CM  = 顧客加重の市場調整 21 日／63 日リターン（0.6/0.4 でブレンド）
    gap = z(CM) − z(自社の同期間市場調整リターン)（過小反応の度合い）
合成 = Σ w_k·Ring_k / Σ w_k（利用可能リングのみ、w = A:0.40 B:0.25 C:0.35）
       ＋ 3 リング全達成ボーナス。上位 TOP_FRACTION を候補とする。
"""
from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
import pandas as pd

from .. import config
from ..universe import NODE_BY_SYMBOL
from . import supply_chain as sc
from .text import plain_summary

RING_WEIGHTS = {"A": 0.40, "B": 0.25, "C": 0.35}
RING_LABELS = {"A": "決算サプライズ", "B": "経営陣の自信", "C": "取引先の追い風"}


def _pct_rank(s: pd.Series) -> pd.Series:
    return s.rank(pct=True, method="average")


def _freshness(days: int | float | None) -> float:
    if days is None or (isinstance(days, float) and math.isnan(days)):
        return 0.0
    return float(max(0.0, 1.0 - days / config.SIGNAL_DECAY_DAYS))


def _fmt_pct(x: float | None, digits: int = 1) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x * 100:+.{digits}f}%"


def ring_a_scores(events: pd.DataFrame, screened: list[str]) -> dict[str, dict]:
    """銘柄ごとの直近イベントから Ring A。"""
    out: dict[str, dict] = {}
    if events.empty:
        return {s: {"score": config.NEUTRAL_SCORE, "available": False, "reason": "決算データなし"} for s in screened}
    ev = events.dropna(subset=["day0"]).sort_values("day0")
    latest_any = ev.groupby("symbol").tail(1).set_index("symbol")
    latest_valid = ev.dropna(subset=["sue_rank"]).groupby("symbol").tail(1).set_index("symbol")
    for s in screened:
        if s not in latest_any.index:
            out[s] = {"score": config.NEUTRAL_SCORE, "available": False, "reason": "決算データなし"}
            continue
        if s not in latest_valid.index:
            r0 = latest_any.loc[s]
            out[s] = {"score": config.NEUTRAL_SCORE, "available": False,
                      "reason": "サプライズを計算できる予想 EPS・実績 EPS が不足",
                      "event_date": r0["date"].strftime("%Y-%m-%d")}
            continue
        r = latest_valid.loc[s]
        rank = r.get("sue_rank")
        days = r.get("days_since_day0")
        if days is None or (isinstance(days, float) and np.isnan(days)) or days > 250:
            out[s] = {"score": config.NEUTRAL_SCORE, "available": False,
                      "reason": "1年以内の決算データがありません（Yahoo 未収録）", "event_date": r["date"].strftime("%Y-%m-%d")}
            continue
        pending = latest_any.loc[s]["date"] > r["date"]   # より新しい決算があるが実績未収録
        note = "より新しい決算の実績 EPS が未収録のため、ひとつ前の決算で評価" if pending else ""
        fresh = _freshness(days)
        score = 50.0 + (float(rank) - 0.5) * 100.0 * fresh
        out[s] = {
            "score": round(score, 1), "available": True, "sue_rank": round(float(rank), 3),
            "freshness": round(fresh, 3), "days_since": int(days) if days is not None and not np.isnan(days) else None,
            "event_date": r["date"].strftime("%Y-%m-%d"), "day0": r["day0"].strftime("%Y-%m-%d"),
            "eps_estimate": _num(r.get("eps_estimate")), "eps_actual": _num(r.get("eps_actual")),
            "surprise_pct": _num(r.get("surprise_pct")), "sue_analyst": _num(r.get("sue_analyst")),
            "sue_srw": _num(r.get("sue_srw")), "sue_decile": _num(r.get("sue_decile")),
            "sue_tercile": _num(r.get("sue_tercile")), "txt_tercile": _num(r.get("txt_tercile")), "sue_txt": _num(r.get("sue_txt")),
            "reaction_0_1": _num(r.get("reaction_0_1")),
            "car_path": r.get("car_path") if isinstance(r.get("car_path"), list) else None,
            "reason": (note or "") if fresh > 0 else "直近の決算から60営業日以上経過（効果は薄れています）",
        }
    return out


def ring_b_scores(latest_text: dict[str, dict], screened: list[str]) -> dict[str, dict]:
    out = {}
    for s in screened:
        t = latest_text.get(s)
        if not t:
            out[s] = {"score": config.NEUTRAL_SCORE, "available": False, "reason": "決算説明テキスト未登録"}
            continue
        out[s] = {
            "score": round(50.0 + 50.0 * float(t["sue_txt"]), 1), "available": True,
            "sue_txt": round(float(t["sue_txt"]), 4), "date": t["date"], "kind": t.get("kind", "manual"),
            "backend": t.get("backend"), "model": t.get("model"), "features": t.get("features"),
            "summary": plain_summary(t.get("features", {})),
        }
    return out


def ring_c_scores(prices: pd.DataFrame, adj_daily: pd.DataFrame, weights: dict[str, dict[str, float]],
                  screened: list[str]) -> dict[str, dict]:
    """顧客モメンタムと過小反応ギャップから Ring C。"""
    def trailing_adj(days: int) -> pd.Series:
        out = {}
        for c in adj_daily.columns:
            s = adj_daily[c].dropna()
            out[c] = float(s.iloc[-days:].sum()) if len(s) >= days else np.nan
        return pd.Series(out)

    r21, r63 = trailing_adj(config.CM_FORMATION_DAYS), trailing_adj(config.CM_FORMATION_DAYS_LONG)
    cm21 = sc.customer_momentum(pd.DataFrame([r21]), weights).iloc[0] if weights else pd.Series(dtype=float)
    cm63 = sc.customer_momentum(pd.DataFrame([r63]), weights).iloc[0] if weights else pd.Series(dtype=float)
    cm = (0.6 * cm21 + 0.4 * cm63).dropna()
    own = (0.6 * r21 + 0.4 * r63)
    gap = sc.underreaction_gap(cm, own)
    pct_cm, pct_gap = _pct_rank(cm), _pct_rank(gap)
    out = {}
    for s in screened:
        if s not in cm.index or s not in gap.index:
            out[s] = {"score": config.NEUTRAL_SCORE, "available": False,
                      "reason": "主要な顧客の情報が無い（有報に 10% 以上の相手先開示なし）"}
            continue
        score = 100.0 * (0.6 * float(pct_cm[s]) + 0.4 * float(pct_gap[s]))
        custs = []
        for c, w in sorted(weights[s].items(), key=lambda kv: -kv[1]):
            custs.append({"symbol": c, "name": NODE_BY_SYMBOL.get(c).name if c in NODE_BY_SYMBOL else c,
                          "weight": round(w, 3), "ret21": _num(r21.get(c)), "ret63": _num(r63.get(c))})
        out[s] = {
            "score": round(score, 1), "available": True,
            "cm21": _num(cm21.get(s)), "cm63": _num(cm63.get(s)), "cm": round(float(cm[s]), 4),
            "own21": _num(r21.get(s)), "own63": _num(r63.get(s)), "gap_z": round(float(gap[s]), 3),
            "pct_cm": round(float(pct_cm[s]), 3), "pct_gap": round(float(pct_gap[s]), 3),
            "customers": custs,
        }
    return out


def _num(v):
    try:
        if v is None:
            return None
        f = float(v)
        return None if math.isnan(f) else round(f, 5)
    except Exception:
        return None


def composite(a: dict, b: dict, c: dict) -> dict:
    parts = []
    for k, ring in (("A", a), ("B", b), ("C", c)):
        if ring.get("available"):
            parts.append((RING_WEIGHTS[k], float(ring["score"])))
    if not parts:
        return {"score": config.NEUTRAL_SCORE, "n_signals": 0, "all_closed": False}
    wsum = sum(w for w, _ in parts)
    score = sum(w * s for w, s in parts) / wsum
    closed = [k for k, ring in (("A", a), ("B", b), ("C", c)) if ring.get("available") and ring["score"] >= config.RING_CLOSE_THRESHOLD]
    all_closed = len(closed) == 3
    if all_closed:
        score += config.ALL_RINGS_BONUS
    # 信号が少ない銘柄は中立へ僅かに引き戻す（情報量ペナルティ）
    shrink = {1: 0.70, 2: 0.90, 3: 1.0}[len(parts)]
    score = 50.0 + (score - 50.0) * shrink
    return {"score": round(min(score, 100.0), 1), "n_signals": len(parts), "closed": closed, "all_closed": all_closed}


def plain_reasons(sym: str, a: dict, b: dict, c: dict, comp: dict) -> tuple[list[str], list[str]]:
    why, risks = [], []
    if a.get("available"):
        sp = a.get("surprise_pct")
        rank = a.get("sue_rank", 0.5)
        # アナリスト予想が無い銘柄（季節ランダムウォーク基準）は予想比が None
        sp_txt = f"予想比 {sp:+.1f}%" if sp is not None else "前年同期比のサプライズ（アナリスト予想なし）"
        if a["score"] >= config.RING_CLOSE_THRESHOLD:
            why.append(f"{a['event_date']}の決算で、利益が事前の予想を{'大きく' if (sp or 0) > 15 else ''}上回りました"
                       f"（{sp_txt}、同時期の銘柄の中で上位{max(1, int(round((1 - rank) * 100)))}%）。"
                       "研究では、こうした『良い驚き』の後は株価がじわじわ上がり続ける傾向があります。")
        elif a["score"] <= 100 - config.RING_CLOSE_THRESHOLD:
            risks.append(f"直近の決算は予想を下回りました（{sp_txt}）。ドリフトは下向きになりやすい局面です。")
        if a.get("freshness", 1) < 0.35 and a.get("days_since"):
            risks.append(f"決算から{a['days_since']}営業日経過。サプライズの効果は薄れつつあります。")
    else:
        risks.append("決算サプライズを計算できるデータがありません（予想 EPS が未収録）。")
    if b.get("available"):
        if b["score"] >= config.RING_CLOSE_THRESHOLD:
            why.append(f"決算説明の言葉づかいが前向きで、見通しを言い切る表現が多めです（テキスト由来スコア {b['score']:.0f}/100）。"
                       "研究では、数字だけでなく『語り口』にもドリフトの情報が含まれます。")
        elif b["score"] <= 100 - config.RING_CLOSE_THRESHOLD:
            risks.append("決算説明の語り口が慎重・弱気寄りです。数字が良くても経営陣が自信を示していない可能性があります。")
    else:
        risks.append("決算説明のテキストが未登録のため、『経営陣の自信』は中立扱いです。テキストを貼り付けると精度が上がります。")
    if c.get("available"):
        top = c["customers"][0] if c.get("customers") else None
        if c["score"] >= config.RING_CLOSE_THRESHOLD and top:
            why.append(f"主要な取引先（{top['name']} など）の株価がここ1〜3か月で市場より{_fmt_pct(c.get('cm'))}強く、"
                       f"一方この銘柄自身は{_fmt_pct(c.get('own21'))}（直近21日）と出遅れています。"
                       "研究では、取引先の好調は1か月ほど遅れてサプライヤーに波及します。")
        elif c["score"] <= 100 - config.RING_CLOSE_THRESHOLD:
            risks.append("主要な取引先の株価が弱く、追い風は期待しにくい局面です。")
    else:
        risks.append("主要な取引先の開示が無く、『取引先の追い風』は中立扱いです。")
    if comp.get("all_closed"):
        why.insert(0, "3つのシグナルがすべて点灯しています。研究上、最もドリフトが強く出やすい組み合わせです。")
    return why, risks


def volatility_metrics(daily: pd.DataFrame, symbols: list[str]) -> dict[str, dict]:
    """年率ボラ（60 日）と 20 日の日次 σ、損切り目安。
    損切り目安 = clamp(2.2·σ20·√10, 5%, 15%)  … 約 2 週間の通常変動の 2 倍強。"""
    out = {}
    for s in symbols:
        if s not in daily.columns:
            continue
        r = daily[s].dropna()
        if len(r) < 25:
            continue
        v60 = float(r.iloc[-config.VOL_DAYS:].std(ddof=1) * math.sqrt(250)) if len(r) >= 30 else float("nan")
        s20 = float(r.iloc[-20:].std(ddof=1))
        stop = min(0.15, max(0.05, 2.2 * s20 * math.sqrt(10)))
        out[s] = {"vol60": None if math.isnan(v60) else round(v60, 4), "sigma20": round(s20, 5), "stop_pct": round(stop, 3)}
    return out


def build_screen(prices: pd.DataFrame, adj_daily: pd.DataFrame, events: pd.DataFrame,
                 latest_text: dict[str, dict], weights: dict[str, dict[str, float]],
                 screened: list[str], info: dict[str, dict], sparklines: dict[str, list],
                 vol: dict[str, dict] | None = None) -> dict:
    vol = vol or {}
    A = ring_a_scores(events, screened)
    B = ring_b_scores(latest_text, screened)
    C = ring_c_scores(prices, adj_daily, weights, screened)
    last_px = prices.ffill().iloc[-1]
    rows = []
    for s in screened:
        if s not in prices.columns:
            continue
        comp = composite(A[s], B[s], C[s])
        why, risks = plain_reasons(s, A[s], B[s], C[s], comp)
        node = NODE_BY_SYMBOL.get(s)
        inf = info.get(s) or {}
        v = vol.get(s, {})
        rows.append({
            "symbol": s, "code": s.replace(".T", ""), "name": node.name if node else s, "sector": node.sector if node else "",
            "price": _num(last_px.get(s)), "market_cap": _num(inf.get("market_cap")), "turnover": _num(inf.get("turnover")),
            "vol60": v.get("vol60"), "stop_pct": v.get("stop_pct"),
            "rings": {"A": A[s], "B": B[s], "C": C[s]},
            "composite": comp, "why": why, "risks": risks,
            "sparkline": sparklines.get(s, []),
        })
    # 値動きの大きさ: 年率ボラの三分位（大/中/小）
    vols = sorted(r["vol60"] for r in rows if r["vol60"] is not None)
    if len(vols) >= 9:
        q1, q2 = vols[len(vols) // 3], vols[2 * len(vols) // 3]
    else:
        q1 = q2 = None
    for r in rows:
        vv = r["vol60"]
        r["move_label"] = None if vv is None or q1 is None else ("小" if vv < q1 else "中" if vv < q2 else "大")
    rows.sort(key=lambda r: (-r["composite"]["score"], -r["composite"]["n_signals"], r["symbol"]))
    n_top = max(config.MIN_CANDIDATES, int(math.ceil(len(rows) * config.TOP_FRACTION)))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        r["candidate"] = i < n_top
        if i >= config.SPARK_TOP_N:            # JSON を軽くする: 上位以外は経路データを落とす
            r["sparkline"] = []
            r["rings"]["A"].pop("car_path", None)
    return {"rows": rows, "n_top": n_top, "n_screened": len(rows),
            "ring_labels": RING_LABELS, "ring_weights": RING_WEIGHTS,
            "close_threshold": config.RING_CLOSE_THRESHOLD, "spark_top_n": config.SPARK_TOP_N,
            "vol_terciles": [q1, q2]}


# ---------------------------------------------------------------------------
# 「ひとつ買うなら」の選定軸: 期待ドリフト ÷ 想定変動
#   E[CAR60 | cell] を条件表（SUE 三分位 × 語り口三分位）から取り、セルの n が小さいときは
#   同じ SUE 三分位の行平均へ縮小推定（Bayesian shrinkage, k=30）。
#   残り期間 = (60 − 経過営業日) / 60 で按分し、想定変動 σ_60d = vol60 × √(60/250) で割る。
#   決算またぎ・薄商いは減点。研究上のドリフトはポートフォリオ平均なので、個別銘柄では
#   「期待値の目安」であって予測ではない、と UI で明示する。
# ---------------------------------------------------------------------------
SHRINK_K = 100                 # 語り口セルの上乗せに使う縮小推定の強さ
TEXT_CELL_MIN_N = 20
TEXT_CELL_MIN_T = 1.5
TEXT_TILT = 0.3                # 検証前の語り口の傾き（土台 × TEXT_TILT × SUE.txt、最大 ±30%）
BASE_CELL_MIN_N = 100
EARNINGS_INSIDE_PENALTY = 0.85
THIN_TURNOVER = 3e8
THIN_PENALTY = 0.9
VOL_LABEL = {1: "小", 2: "中", 3: "大"}
TERCILE_LABEL = {1: "弱い", 2: "ふつう", 3: "強い"}


def _vol_tercile_of(vol60: float | None, thresholds) -> int | None:
    if vol60 is None or not thresholds or thresholds[0] is None:
        return None
    return 1 if vol60 < thresholds[0] else 2 if vol60 < thresholds[1] else 3


def attach_expectations(scr: dict, cond_table: list[dict], decile_vol_table: list[dict] | None = None,
                        vol_thresholds=None, as_of: str | None = None) -> dict:
    """期待ドリフトの推定。

    土台   : E0 = 平均 CAR60 [SUE 十分位 × ボラ三分位]（n ≥ 100）、無ければ十分位全体、無ければ SUE 三分位。
             （Mendenhall 2004: 裁定リスク＝ボラが高い銘柄ほどドリフトが大きい、を実測で反映）
    上乗せ : 語り口セル（SUE 三分位 × SUE.txt 三分位）が n ≥ 20 かつ |t| ≥ 1.5 のときのみ、
             同 SUE 三分位の行平均との差分 Δ を n/(n+100) で縮小して加える。
    残り   : (60 − 経過営業日)/60 で按分。減点: 保有中に決算 ×0.85、売買代金 3 億円/日未満 ×0.9。
    score  : 残り期待ドリフト × 減点（= 選定の主軸）。ratio = 残り期待 ÷ σ_60d は参考表示。
    """
    by_txt = {(r["sue_tercile"], r["txt_tercile"]): r for r in cond_table}
    by_dv = {(r["sue_decile"], r["vol_tercile"]): r for r in (decile_vol_table or [])}
    H = config.CAR_HORIZON
    for row in scr["rows"]:
        A = row["rings"]["A"]
        if not A.get("available") or A.get("sue_tercile") is None:
            row["expected"] = None
            continue
        st = int(A["sue_tercile"])
        dec = int(A["sue_decile"]) if A.get("sue_decile") is not None else None
        vt = _vol_tercile_of(row.get("vol60"), vol_thresholds)
        base, basis = None, ""
        if dec is not None and vt is not None and (dec, vt) in by_dv and by_dv[(dec, vt)]["n"] >= BASE_CELL_MIN_N:
            base = by_dv[(dec, vt)]; basis = f"サプライズ十分位{dec}（上位{(11 - dec) * 10}%）× 値動き「{VOL_LABEL[vt]}」"
        elif dec is not None and (dec, None) in by_dv and by_dv[(dec, None)]["n"] >= BASE_CELL_MIN_N:
            base = by_dv[(dec, None)]; basis = f"サプライズ十分位{dec}（上位{(11 - dec) * 10}%）"
        elif (st, None) in by_txt and by_txt[(st, None)].get("mean_car") is not None:
            base = by_txt[(st, None)]; basis = f"サプライズ「{TERCILE_LABEL[st]}」"
        if not base or base.get("mean_car") is None:
            row["expected"] = None
            continue
        mean, hit, n_used = float(base["mean_car"]), float(base.get("hit_rate") or 0), int(base["n"])
        # 語り口の上乗せ（検証を通ったセルのみ）
        tt = int(A["txt_tercile"]) if A.get("txt_tercile") is not None else None
        text_adj, text_note = 0.0, ""
        cell = by_txt.get((st, tt)) if tt is not None else None
        rowc = by_txt.get((st, None))
        if cell and rowc and cell.get("mean_car") is not None and rowc.get("mean_car") is not None \
                and cell["n"] >= TEXT_CELL_MIN_N and abs(cell.get("t") or 0) >= TEXT_CELL_MIN_T:
            delta = float(cell["mean_car"] - rowc["mean_car"])
            text_adj = delta * cell["n"] / (cell["n"] + SHRINK_K)
            text_note = f"語り口「{TERCILE_LABEL[tt]}」の上乗せ {text_adj * 100:+.2f}%（{cell['n']} 件, t={cell['t']}）"
        else:
            # 検証済みセルが無い間は、論文（Meursault et al. 2023）の知見に基づく傾きとして SUE.txt を反映
            stxt = A.get("sue_txt")
            if stxt is None and row["rings"]["B"].get("available"):
                stxt = row["rings"]["B"].get("sue_txt")
            if stxt is not None and mean != 0:
                text_adj = mean * TEXT_TILT * float(stxt)
                text_note = (f"経営陣の自信（SUE.txt {float(stxt):+.2f}）による傾き {text_adj * 100:+.2f}%"
                             f"（論文の知見を反映。本データでの検証は件数待ち）")
            elif tt is not None:
                text_note = f"語り口「{TERCILE_LABEL[tt]}」の効果は、まだ件数が足りず期待値に加えていません"
        mean_total = mean + text_adj
        days = A.get("days_since") or 0
        elapsed = max(0, days - config.CAR_START_OFFSET)
        remaining_frac = max(0.0, (H - elapsed) / H)
        drift_remaining = mean_total * remaining_frac
        vol60 = row.get("vol60")
        sigma_h = (vol60 * math.sqrt(H / 250.0)) if vol60 else None
        ratio = (drift_remaining / sigma_h) if sigma_h and sigma_h > 0 else None
        factors, notes = 1.0, []
        remaining_days = int(round(H * remaining_frac))
        ne = row.get("next_earnings")
        if ne and as_of:
            try:
                gap = (pd.Timestamp(ne) - pd.Timestamp(as_of)).days
                if 0 <= gap <= remaining_days * 1.45:
                    factors *= EARNINGS_INSIDE_PENALTY; notes.append("保有中に決算")
            except Exception:
                pass
        if row.get("turnover") is not None and row["turnover"] < THIN_TURNOVER:
            factors *= THIN_PENALTY; notes.append("売買代金が薄い")
        row["expected"] = {
            "drift_full": round(mean_total, 5), "drift_base": round(mean, 5), "text_adj": round(text_adj, 5),
            "drift_remaining": round(float(drift_remaining), 5), "remaining_days": remaining_days,
            "sigma_h": None if sigma_h is None else round(float(sigma_h), 5),
            "ratio": None if ratio is None else round(float(ratio), 4),
            "score": round(float(drift_remaining * factors), 5),
            "hit_rate": round(hit, 4), "n": n_used, "basis": basis, "text_note": text_note,
            "t": base.get("t"), "sue_tercile": st, "txt_tercile": tt, "sue_decile": dec, "vol_tercile": vt, "penalties": notes,
        }
    return scr
