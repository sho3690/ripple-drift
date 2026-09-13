"""決算イベント（発表日・EPS 予想/実績）と基本情報の取得層。

yfinance の `get_earnings_dates` は、東証銘柄でもアナリスト EPS コンセンサスと実績 EPS を
四半期ごとに返す。これを SUE（標準化予想外利益）と PEAD イベントスタディの元データにする。
発表時刻は米国東部時間で返るため JST に変換し、「市場が最初に反応できる営業日」を
event day 0 と定義する（15:00 JST 以降の発表は翌営業日を day 0 とする）。
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .. import config

log = logging.getLogger(__name__)

EARN_DIR = config.CACHE_DIR / "earnings"
INFO_DIR = config.CACHE_DIR / "info"
EARN_DIR.mkdir(exist_ok=True)
INFO_DIR.mkdir(exist_ok=True)

JST_CLOSE_HOUR = 15


def _fresh(path, ttl: timedelta) -> bool:
    if not path.exists():
        return False
    try:
        meta = json.loads(path.read_text())
        return datetime.now() - datetime.fromisoformat(meta["fetched_at"]) < ttl
    except Exception:
        return False


def _fetch_earnings(symbol: str) -> list[dict]:
    import yfinance as yf

    last_err = None
    for attempt in range(3):
        try:
            df = yf.Ticker(symbol).get_earnings_dates(limit=config.EARNINGS_LIMIT)
            break
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    else:
        log.warning("決算日の取得に失敗 %s: %s", symbol, last_err)
        return []
    if df is None or df.empty:
        return []
    rows = []
    for ts, r in df.iterrows():
        ts = pd.Timestamp(ts)
        if ts.tzinfo is None:
            ts = ts.tz_localize("America/New_York")
        jst = ts.tz_convert("Asia/Tokyo")
        after_close = jst.hour >= JST_CLOSE_HOUR
        rows.append({
            "announced_jst": jst.strftime("%Y-%m-%d %H:%M"),
            "date": jst.strftime("%Y-%m-%d"),
            "after_close": bool(after_close),
            "eps_estimate": _f(r.get("EPS Estimate")),
            "eps_actual": _f(r.get("Reported EPS")),
            "surprise_pct": _f(r.get("Surprise(%)")),
        })
    return rows


def _f(v):
    try:
        v = float(v)
        return None if np.isnan(v) else v
    except Exception:
        return None


def load_earnings(symbol: str, force: bool = False) -> pd.DataFrame:
    path = EARN_DIR / f"{symbol}.json"
    ttl = timedelta(hours=config.EARNINGS_CACHE_TTL_HOURS)
    if not force and _fresh(path, ttl):
        rows = json.loads(path.read_text())["rows"]
    else:
        rows = _fetch_earnings(symbol)
        path.write_text(json.dumps({"fetched_at": datetime.now().isoformat(), "rows": rows}, ensure_ascii=False))
    df = pd.DataFrame(rows, columns=["announced_jst", "date", "after_close", "eps_estimate", "eps_actual", "surprise_pct"])
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df["symbol"] = symbol
    return df.sort_values("date").reset_index(drop=True)


def load_all_earnings(symbols: list[str], force: bool = False, workers: int = 6,
                      progress=None) -> pd.DataFrame:
    frames = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(load_earnings, s, force): s for s in symbols}
        for fut in as_completed(futs):
            done += 1
            if progress:
                progress(done, len(symbols))
            try:
                df = fut.result()
                if not df.empty:
                    frames.append(df)
            except Exception as e:
                log.warning("決算データ失敗 %s: %s", futs[fut], e)
    if not frames:
        return pd.DataFrame(columns=["symbol", "date", "after_close", "eps_estimate", "eps_actual", "surprise_pct"])
    return pd.concat(frames, ignore_index=True).sort_values(["symbol", "date"]).reset_index(drop=True)


def _fetch_info(symbol: str) -> dict:
    import yfinance as yf

    out: dict = {}
    try:
        fi = yf.Ticker(symbol).fast_info
        out["market_cap"] = _f(getattr(fi, "market_cap", None))
        out["last_price"] = _f(getattr(fi, "last_price", None))
        out["currency"] = getattr(fi, "currency", None)
        out["shares"] = _f(getattr(fi, "shares", None))
        out["year_high"] = _f(getattr(fi, "year_high", None))
        out["year_low"] = _f(getattr(fi, "year_low", None))
    except Exception as e:
        log.debug("fast_info 失敗 %s: %s", symbol, e)
    return out


def load_info(symbol: str, force: bool = False) -> dict:
    path = INFO_DIR / f"{symbol}.json"
    if not force and _fresh(path, timedelta(days=config.INFO_CACHE_TTL_DAYS)):
        return json.loads(path.read_text())["info"]
    info = _fetch_info(symbol)
    path.write_text(json.dumps({"fetched_at": datetime.now().isoformat(), "info": info}, ensure_ascii=False))
    return info


def load_all_info(symbols: list[str], force: bool = False, workers: int = 6, progress=None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(load_info, s, force): s for s in symbols}
        for fut in as_completed(futs):
            done += 1
            if progress:
                progress(done, len(symbols))
            try:
                out[futs[fut]] = fut.result()
            except Exception:
                out[futs[fut]] = {}
    return out
