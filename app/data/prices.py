"""株価データ層。yfinance から調整後終値を一括取得し、ローカルにキャッシュする。

市場ごとに取引カレンダーが異なる（東証・NYSE・KRX・TWSE）ため、リターン計算は
「各系列自身の営業日」で行い、その後に和集合インデックスへ再配置する。市場調整リターン
（market-adjusted return）は同一市場のベンチマークと同じ日付で差し引く。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config

log = logging.getLogger(__name__)

PRICE_CACHE = config.CACHE_DIR / "prices.pkl"
PRICE_META = config.CACHE_DIR / "prices.meta.json"


def _cache_fresh(symbols: list[str]) -> bool:
    if not (PRICE_CACHE.exists() and PRICE_META.exists()):
        return False
    try:
        meta = json.loads(PRICE_META.read_text())
        fetched = datetime.fromisoformat(meta["fetched_at"])
        if datetime.now() - fetched > timedelta(hours=config.PRICE_CACHE_TTL_HOURS):
            return False
        return set(symbols).issubset(set(meta["symbols"]))
    except Exception:
        return False


def load_prices(symbols: list[str], start: str = config.PRICE_START, force: bool = False) -> pd.DataFrame:
    """調整後終値の DataFrame（index=日付, columns=シンボル）。欠損列は落とす。"""
    symbols = sorted(set(symbols))
    if not force and _cache_fresh(symbols):
        df = pd.read_pickle(PRICE_CACHE)
        return df[[c for c in symbols if c in df.columns]]

    import yfinance as yf

    log.info("株価を取得中: %d 銘柄 (%s〜)", len(symbols), start)
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            raw = yf.download(symbols, start=start, auto_adjust=True, progress=False,
                              group_by="column", threads=True)
            break
        except Exception as e:  # ネットワーク一時障害
            last_err = e
            time.sleep(2.0 * (attempt + 1))
    else:
        raise RuntimeError(f"株価の取得に失敗しました: {last_err}")

    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"].copy()
    else:  # 単一銘柄
        close = raw[["Close"]].copy()
        close.columns = symbols
    close.index = pd.to_datetime(close.index).tz_localize(None)
    close = close.sort_index()
    close = close.dropna(axis=1, how="all")
    missing = sorted(set(symbols) - set(close.columns))
    if missing:
        log.warning("株価が取得できなかった銘柄: %s", missing)

    close.to_pickle(PRICE_CACHE)
    PRICE_META.write_text(json.dumps({
        "fetched_at": datetime.now().isoformat(),
        "symbols": symbols,
        "missing": missing,
        "start": start,
    }, ensure_ascii=False))
    return close


def price_meta() -> dict:
    try:
        return json.loads(PRICE_META.read_text())
    except Exception:
        return {}


def sanitize_prices(prices: pd.DataFrame, threshold: float = 0.5, max_gap: int = 5) -> tuple[pd.DataFrame, list[str]]:
    """未調整の株式分割・データ不良による「急落→急騰（またはその逆）で元に戻る」パターンを除去する。

    |r_t| > threshold の日と、その後 max_gap 営業日以内に逆符号で概ね打ち消す日があれば、
    その区間の価格を NaN にする（リターンは区間をまたいで計算される）。
    """
    out = prices.copy()
    notes: list[str] = []
    for c in out.columns:
        s = out[c].dropna()
        if len(s) < 10:
            continue
        r = s.pct_change()
        spikes = list(np.where(r.abs().to_numpy() > threshold)[0])
        removed = set()
        for i in spikes:
            for j in spikes:
                if i < j <= i + max_gap and r.iloc[i] * r.iloc[j] < 0:
                    net = (1 + r.iloc[i]) * (1 + r.iloc[j])
                    if 0.6 <= net <= 1.6:
                        removed.update(range(i, j))
        if removed:
            idx = s.index[sorted(removed)]
            out.loc[idx, c] = np.nan
            notes.append(f"{c}: {idx.min().date()}〜{idx.max().date()} の {len(idx)} 日を異常値として除外")
    return out, notes


def is_clean_series(prices: pd.DataFrame, symbol: str, max_abs_return: float = 0.3) -> bool:
    if symbol not in prices.columns:
        return False
    s = prices[symbol].dropna()
    if len(s) < 250:
        return False
    return bool((s.pct_change().dropna().abs() <= max_abs_return).all())


def daily_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """各系列自身の営業日で単純リターンを計算し、和集合インデックスへ戻す。"""
    out = {}
    for c in prices.columns:
        s = prices[c].dropna()
        out[c] = s.pct_change()
    return pd.DataFrame(out).reindex(prices.index)


def market_adjusted(returns: pd.DataFrame, bench_of: dict[str, str]) -> pd.DataFrame:
    """r_i − r_m。bench_of[symbol] = ベンチマークシンボル。ベンチマーク列が無ければ生リターン。"""
    out = {}
    for c in returns.columns:
        b = bench_of.get(c)
        if b and b in returns.columns and b != c:
            out[c] = returns[c] - returns[b]
        else:
            out[c] = returns[c]
    return pd.DataFrame(out).reindex(returns.index)


def _period_returns(prices: pd.DataFrame, rule: str) -> pd.DataFrame:
    """期間末値ベースのリターン（系列ごとに欠損を除いてリサンプル）。"""
    out = {}
    for c in prices.columns:
        s = prices[c].dropna()
        if s.empty:
            continue
        out[c] = s.resample(rule).last().pct_change()
    return pd.DataFrame(out)


def monthly_returns(prices: pd.DataFrame) -> pd.DataFrame:
    return _period_returns(prices, "ME")


def weekly_returns(prices: pd.DataFrame) -> pd.DataFrame:
    return _period_returns(prices, "W-FRI")


def trailing_return(prices: pd.DataFrame, days: int) -> pd.Series:
    """直近 `days` 営業日の累積リターン（系列自身の営業日ベース）。"""
    out = {}
    for c in prices.columns:
        s = prices[c].dropna()
        if len(s) > days:
            out[c] = float(s.iloc[-1] / s.iloc[-1 - days] - 1.0)
        else:
            out[c] = np.nan
    return pd.Series(out)


def last_prices(prices: pd.DataFrame) -> pd.Series:
    return prices.ffill().iloc[-1]


def last_dates(prices: pd.DataFrame) -> pd.Series:
    return prices.apply(lambda s: s.dropna().index.max())


def sparkline(prices: pd.DataFrame, symbol: str, days: int = 90) -> list[list]:
    s = prices[symbol].dropna().iloc[-days:]
    return [[d.strftime("%Y-%m-%d"), round(float(v), 2)] for d, v in s.items()]
