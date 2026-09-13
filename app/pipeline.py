"""パイプライン: データ取得 → モデル推定 → スクリーン → output/results.json。

UI はこのファイルの出力だけを読む。`python -m app.pipeline` で単体実行できる。
"""
from __future__ import annotations

import json
import logging
import math
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import config
from .data import edinet, fundamentals, prices as pricemod, transcripts, universe_screen
from .models import pead, screen, sue, supply_chain as sc, text as textmod
from .universe import EDGES, NODES, NODE_BY_SYMBOL, SEED_SYMBOLS, Edge, benchmarks_for, register_dynamic

log = logging.getLogger("ripple.pipeline")

MODEL_PATH = config.CACHE_DIR / "text_model.json"
EDINET_TEXT_CACHE = config.CACHE_DIR / "edinet_texts.json"
EDINET_EDGE_CACHE = config.CACHE_DIR / "edinet_edges.json"


class Progress:
    def __init__(self, cb=None):
        self.cb = cb
        self.stage = ""
        self.pct = 0.0
        self.message = ""

    def __call__(self, stage: str, pct: float, message: str = ""):
        self.stage, self.pct, self.message = stage, pct, message
        log.info("[%3.0f%%] %s %s", pct, stage, message)
        if self.cb:
            self.cb(stage, pct, message)


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        f = float(o)
        return None if math.isnan(f) else f
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.strftime("%Y-%m-%d")
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, float) and math.isnan(o):
        return None
    return str(o)


def _scrub(obj):
    """NaN を None に、numpy を Python 型に。"""
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub(v) for v in obj]
    if isinstance(obj, float) and math.isnan(obj):
        return None
    if isinstance(obj, (np.floating,)):
        f = float(obj)
        return None if math.isnan(f) else f
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.strftime("%Y-%m-%d")
    return obj


def merge_edinet_edges(base: list[Edge], edinet_edges: list[dict]) -> list[Edge]:
    """EDINET 実測の売上依存度で seed を上書き（無い組合せは追加）。"""
    by_key = {(e.supplier, e.customer): e for e in base}
    for d in edinet_edges:
        key = (d["supplier"], d["customer"])
        share = d.get("share")
        w = float(share) if share else (by_key[key].weight if key in by_key else 0.15)
        by_key[key] = Edge(d["supplier"], d["customer"], max(min(w, 1.0), 0.01), "edinet",
                           f"有報 {d.get('submitted', '')[:10]} 主要な顧客: {d.get('customer_name', '')}")
    return list(by_key.values())


def attach_text_scores(events: pd.DataFrame, texts: dict[str, list[dict]], model: textmod.TextSurpriseModel,
                       max_gap_days: int = 45) -> tuple[pd.DataFrame, dict[str, dict], list[dict]]:
    """テキストをイベントへ紐付けて採点。返り値: (events, 銘柄別最新テキスト採点, 全採点リスト)。"""
    ev = events.copy()
    ev["sue_txt"] = np.nan
    ev["txt_backend"] = None
    latest: dict[str, dict] = {}
    scored: list[dict] = []
    for sym, items in texts.items():
        g = ev[ev["symbol"] == sym]
        for it in items:
            d = pd.Timestamp(it["date"])
            s = textmod.score_text(it["text"], model)
            rec = {"symbol": sym, "date": it["date"], "kind": it.get("kind", "manual"), **s}
            scored.append(rec)
            if not g.empty:
                diff = (g["date"] - d).abs()
                j = diff.idxmin()
                if diff.loc[j] <= pd.Timedelta(days=max_gap_days):
                    ev.at[j, "sue_txt"] = s["sue_txt"]
                    ev.at[j, "txt_backend"] = s["backend"]
                    rec["event_date"] = ev.at[j, "date"].strftime("%Y-%m-%d")
            if sym not in latest or it["date"] > latest[sym]["date"]:
                latest[sym] = rec
    return ev, latest, scored


def train_text_model(ev: pd.DataFrame, texts: dict[str, list[dict]]) -> textmod.TextSurpriseModel:
    """ラベル（実現 SUE の符号）付きテキストが十分あればロジスティック回帰を学習。"""
    X, y = [], []
    for sym, items in texts.items():
        g = ev[(ev["symbol"] == sym)].dropna(subset=["sue_rank"])
        if g.empty:
            continue
        for it in items:
            d = pd.Timestamp(it["date"])
            diff = (g["date"] - d).abs()
            j = diff.idxmin()
            if diff.loc[j] > pd.Timedelta(days=45):
                continue
            f = textmod.lexicon_features(it["text"])
            X.append(f.vector()); y.append(1.0 if g.at[j, "sue_rank"] > 0.5 else 0.0)
    model = textmod.TextSurpriseModel()
    if len(y) >= config.MIN_LABELED_FOR_LOGIT and 0 < sum(y) < len(y):
        model.fit(np.array(X), np.array(y))
        MODEL_PATH.write_text(json.dumps(model.to_dict()))
        log.info("SUE.txt ロジット学習: n=%d", len(y))
    elif MODEL_PATH.exists():
        try:
            model = textmod.TextSurpriseModel.from_dict(json.loads(MODEL_PATH.read_text()))
        except Exception:
            model = textmod.TextSurpriseModel()
    return model


def run(progress_cb=None, force: bool = False, use_edinet: bool = True) -> dict:
    t_start = time.time()
    P = Progress(progress_cb)
    warnings: list[str] = []

    # 0. ユニバース ---------------------------------------------------------
    P("universe", 1, "銘柄リストを作成しています")
    dyn_rows: list[dict] = []
    jp_names: dict[str, str] = {}
    if config.UNIVERSE_MODE == "broad":
        try:
            dyn_rows = universe_screen.load_universe(force=force)
        except Exception as e:
            warnings.append(f"銘柄スクリーナーの取得に失敗（同梱リストのみで続行）: {e}")
        jp_names = edinet.filer_name_map()
        if len(jp_names) < 1000 and edinet.read_key():
            P("universe", 2, "日本語社名（EDINET 提出者リスト）を取得しています")
            edinet.prefetch_lists(days_back=400)
            jp_names = edinet.filer_name_map()
    added_nodes = register_dynamic(dyn_rows, jp_names)
    nodes_all = list(NODES) + added_nodes
    all_syms = [n.symbol for n in nodes_all]
    dyn_info = {r["symbol"]: r for r in dyn_rows}

    # 1. 株価 --------------------------------------------------------------
    P("prices", 3, f"株価を取得しています（{len(all_syms)} 銘柄）")
    bench = [config.JP_BENCHMARK, config.JP_INDEX_FALLBACK, config.US_BENCHMARK, config.KR_BENCHMARK, config.TW_BENCHMARK]
    prices = pricemod.load_prices(all_syms + bench, force=force)
    meta = pricemod.price_meta()
    missing_now = [m for m in meta.get("missing", []) if m in SEED_SYMBOLS]
    if missing_now:
        warnings.append("株価が取得できなかった銘柄: " + ", ".join(missing_now))
    prices, clean_notes = pricemod.sanitize_prices(prices)
    warnings.extend(clean_notes)
    available = set(prices.columns)
    screened = [n.symbol for n in nodes_all if n.screened and n.symbol in available]
    if pricemod.is_clean_series(prices, config.JP_BENCHMARK):
        jp_bench = config.JP_BENCHMARK
    else:
        jp_bench = config.JP_INDEX_FALLBACK
        warnings.append(f"ベンチマーク {config.JP_BENCHMARK} のデータ品質が不十分なため {jp_bench} を使用")
    bench_of = {}
    for n in nodes_all:
        bench_of[n.symbol] = jp_bench if n.market == "JP" else benchmarks_for(n.market)
    daily = pricemod.daily_returns(prices)
    adj_daily = pricemod.market_adjusted(daily, bench_of)

    # 2. EDINET（任意） --------------------------------------------------
    P("edinet", 12, "EDINET（有報の主要な顧客・MD&A）を確認しています")
    edges = list(EDGES)
    edinet_status = {"enabled": False, "docs_scanned": 0, "edges_added": 0, "texts_added": 0, "errors": []}
    edinet_texts: dict[str, list[dict]] = {}
    if use_edinet and edinet.read_key():
        try:
            res = edinet.refresh_from_edinet(days_back=400, symbols=(set(all_syms) if config.EDINET_ALL else set(SEED_SYMBOLS)),
                                             progress=lambda n, s: P("edinet", 12 + min(n, 60) * 0.1, f"{s} を解析中"))
            edinet_status.update({"enabled": True, "docs_scanned": res["docs_scanned"], "errors": res["errors"],
                                  "edges_added": len(res["edges"]), "texts_added": sum(len(v) for v in res["texts"].values())})
            EDINET_EDGE_CACHE.write_text(json.dumps(res["edges"], ensure_ascii=False))
            EDINET_TEXT_CACHE.write_text(json.dumps(res["texts"], ensure_ascii=False))
            edges = merge_edinet_edges(edges, res["edges"])
            edinet_texts = res["texts"]
        except Exception as e:  # EDINET は補助なので失敗しても続行
            edinet_status["errors"].append(str(e))
            warnings.append(f"EDINET 連携でエラー: {e}")
    elif EDINET_EDGE_CACHE.exists():
        try:
            edges = merge_edinet_edges(edges, json.loads(EDINET_EDGE_CACHE.read_text()))
            edinet_texts = json.loads(EDINET_TEXT_CACHE.read_text()) if EDINET_TEXT_CACHE.exists() else {}
            edinet_status["enabled"] = "cache"
        except Exception:
            pass

    G = sc.build_graph(NODES, edges, available)
    weights = sc.customer_weights(G)

    # 3. 決算イベント ------------------------------------------------------
    P("earnings", 20, "決算発表日と EPS 予想・実績を取得しています")
    ev_syms = sorted(available & set(all_syms))
    events = fundamentals.load_all_earnings(ev_syms, force=force, workers=5,
                                            progress=lambda d, n: P("earnings", 20 + 30 * d / max(n, 1), f"{d}/{n} 銘柄"))
    events = events[events["eps_actual"].notna() | events["eps_estimate"].notna()].copy()
    # 次回の決算発表予定日（実績未収録かつ株価最終日より後）を銘柄ごとに控える
    last_px_date = prices.index.max()
    upcoming_df = events[(events["date"] > last_px_date) & events["eps_actual"].isna()]
    next_earnings = {sym: g["date"].min().strftime("%Y-%m-%d") for sym, g in upcoming_df.groupby("symbol")}
    events = sue.attach_day0_and_price(events, prices)
    events = events.dropna(subset=["day0"]).reset_index(drop=True)
    events["sue_analyst"] = sue.sue_analyst(events)
    events["sue_srw"] = sue.sue_seasonal_random_walk(events)
    events["sue_rank"] = sue.combine_sue(events)
    events["sue_decile"] = sue.to_decile(events["sue_rank"])
    events["sue_tercile"] = sue.to_tercile(events["sue_rank"])

    # 4. テキスト（SUE.txt） ----------------------------------------------
    P("text", 55, "決算説明テキストを採点しています")
    texts = transcripts.load_transcripts()
    for sym, items in edinet_texts.items():
        for it in items:
            texts.setdefault(sym, []).append({"date": it["date"], "text": it["text"], "kind": it.get("kind", "有報MD&A")})
    model = train_text_model(events, texts)
    events, latest_text, scored_texts = attach_text_scores(events, texts, model)
    q = sue.quarter_bucket(events["date"])
    txt_rank = sue.cross_sectional_rank(events["sue_txt"], q, min_group=6)
    events["txt_tercile"] = sue.to_tercile(txt_rank)
    # 横断面が薄いときは絶対値で三分位
    fallback = events["sue_txt"].notna() & events["txt_tercile"].isna()
    events.loc[fallback, "txt_tercile"] = np.where(events.loc[fallback, "sue_txt"] > 0.15, 3,
                                                   np.where(events.loc[fallback, "sue_txt"] < -0.15, 1, 2))

    # 5. PEAD イベントスタディ -------------------------------------------
    P("pead", 65, "発表後ドリフト（CAR）を計測しています")
    events = pead.compute_car(events, adj_daily)
    complete = events[events["car_complete"]]
    decile_paths = pead.mean_path_by_group(complete, "sue_decile")
    cond_table = pead.conditional_table(complete)
    best = pead.best_condition(cond_table)
    ls = pead.long_short_by_decile(complete)
    reg = pead.drift_regression(complete)
    spill = pead.customer_surprise_spillover(complete, weights)

    # 6. サプライチェーン ------------------------------------------------
    P("supply", 78, "サプライチェーンの波及ラグを推定しています")
    monthly = pricemod.monthly_returns(prices)
    weekly = pricemod.weekly_returns(prices)
    m_adj = pricemod.market_adjusted(monthly, bench_of)
    w_adj = pricemod.market_adjusted(weekly, bench_of)
    cm_m = sc.customer_momentum(m_adj, weights)
    mom_m = sc.own_momentum(m_adj)
    leadlag_m = sc.cross_autocorrelation(m_adj, G, config.LEADLAG_MAX_LAG_MONTHS, "monthly", min_obs=24)
    leadlag_w = sc.cross_autocorrelation(w_adj, G, config.LEADLAG_MAX_LAG_WEEKS, "weekly", min_obs=52)
    fm = sc.fama_macbeth(m_adj, cm_m, mom_m)
    cm_sort = sc.customer_momentum_sort(m_adj, cm_m, n_groups=5)

    # 7. スクリーン --------------------------------------------------------
    P("screen", 88, "候補銘柄を選んでいます")
    # 基本情報: スクリーナー由来はそのまま、同梱銘柄で不足分だけ fast_info を取る
    info: dict[str, dict] = {}
    need_fast = []
    for s_ in screened:
        d = dyn_info.get(s_)
        if d:
            info[s_] = {"market_cap": d.get("market_cap"), "last_price": d.get("price"), "turnover": d.get("turnover")}
        else:
            need_fast.append(s_)
    if need_fast:
        info.update(fundamentals.load_all_info(need_fast, force=force))
    sparks = {s_: pricemod.sparkline(prices, s_, 90) for s_ in screened}
    vol = screen.volatility_metrics(daily, screened)
    scr = screen.build_screen(prices, adj_daily, events, latest_text, weights, screened, info, sparks, vol)
    for r in scr["rows"]:
        r["next_earnings"] = next_earnings.get(r["symbol"])
    candidates = {r["symbol"] for r in scr["rows"] if r["candidate"]}

    # 8. 出力 --------------------------------------------------------------
    P("write", 96, "結果を書き出しています")
    last_dates = pricemod.last_dates(prices)
    as_of = str(pd.Timestamp(last_dates.get(jp_bench, prices.index.max())).date())
    n_events = int(len(events))
    n_an = int(events["sue_analyst"].notna().sum())
    n_txt = int(events["sue_txt"].notna().sum())
    results = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "as_of": as_of,
        "elapsed_sec": round(time.time() - t_start, 1),
        "params": {
            "car_window": [config.CAR_START_OFFSET, config.CAR_START_OFFSET + config.CAR_HORIZON - 1],
            "horizon_days": config.CAR_HORIZON, "top_fraction": config.TOP_FRACTION,
            "close_threshold": config.RING_CLOSE_THRESHOLD, "cm_days": [config.CM_FORMATION_DAYS, config.CM_FORMATION_DAYS_LONG],
            "benchmark": jp_bench,
        },
        "data_quality": {
            "universe": {"mode": config.UNIVERSE_MODE, "n_seed": len(NODES), "n_dynamic": len(added_nodes),
                          "mcap_min": config.UNIVERSE_MCAP_MIN, "min_turnover": config.UNIVERSE_MIN_TURNOVER,
                          "n_jp_names": len(jp_names), **universe_screen.universe_meta()},
            "n_symbols_priced": int(len(available & set(all_syms))), "n_screened": len(screened),
            "n_events": n_events, "n_events_analyst_sue": n_an, "n_events_complete_car": int(len(complete)),
            "n_texts": sum(len(v) for v in texts.values()), "n_events_with_text": n_txt,
            "text_model": "logit" if model.fitted else "prior", "text_model_n": model.n_train,
            "edinet": edinet_status, "missing_prices": missing_now,
            "n_edges": G.number_of_edges(), "n_suppliers": len(weights),
            "price_fetched_at": meta.get("fetched_at"), "warnings": warnings,
        },
        "screen": scr,
        "pead": {
            "decile_paths": decile_paths, "conditional_table": cond_table, "best_condition": best,
            "decile_summary": ls, "regression": reg, "customer_spillover": spill,
        },
        "supply_chain": {
            "leadlag_monthly": leadlag_m.as_dict(), "leadlag_weekly": leadlag_w.as_dict(),
            "fama_macbeth": fm, "cm_quintile_sort": cm_sort,
            "graph": sc.graph_payload(G, candidates),
        },
        "texts": scored_texts,
        "events_recent": _recent_events(events, 120),
    }
    results = _scrub(results)
    config.RESULTS_PATH.write_text(json.dumps(results, ensure_ascii=False, default=_json_default))
    P("done", 100, f"完了（{results['elapsed_sec']} 秒）")
    return results


def _recent_events(events: pd.DataFrame, days: int) -> list[dict]:
    cutoff = events["date"].max() - pd.Timedelta(days=days)
    sub = events[events["date"] >= cutoff].sort_values("date", ascending=False)
    cols = ["symbol", "date", "day0", "eps_estimate", "eps_actual", "surprise_pct", "sue_analyst", "sue_srw",
            "sue_rank", "sue_decile", "sue_txt", "reaction_0_1", "car20", "car60", "days_since_day0"]
    out = []
    for _, r in sub.iterrows():
        d = {c: r.get(c) for c in cols if c in sub.columns}
        d["name"] = NODE_BY_SYMBOL[r["symbol"]].name if r["symbol"] in NODE_BY_SYMBOL else r["symbol"]
        out.append(d)
    return out


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(description="RippleDrift パイプライン")
    ap.add_argument("--force", action="store_true", help="キャッシュを無視して再取得")
    ap.add_argument("--no-edinet", action="store_true", help="EDINET 連携を使わない")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    res = run(force=args.force, use_edinet=not args.no_edinet)
    dq = res["data_quality"]
    print(f"\n完了: 候補 {res['screen']['n_top']} 銘柄 / 審査 {dq['n_screened']} 銘柄 / イベント {dq['n_events']} 件 → {config.RESULTS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
