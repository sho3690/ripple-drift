"""動的ユニバース: Yahoo Finance のスクリーナーで東証上場銘柄を時価総額・流動性で絞り込む。

- region = jp、時価総額 ≥ UNIVERSE_MCAP_MIN の銘柄を時価総額順に取得（250 件ずつページング）。
- 売買代金（価格 × 3 か月平均出来高）≥ UNIVERSE_MIN_TURNOVER で流動性を担保。
- 結果は data/cache/universe.json に 7 日間キャッシュ。
社名は英語（Yahoo）なので、日本語名は EDINET の提出者リスト（edinet.filer_name_map）で上書きする。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta

from .. import config

log = logging.getLogger(__name__)

UNIVERSE_CACHE = config.CACHE_DIR / "universe.json"


def _fresh() -> bool:
    if not UNIVERSE_CACHE.exists():
        return False
    try:
        meta = json.loads(UNIVERSE_CACHE.read_text())
        return datetime.now() - datetime.fromisoformat(meta["fetched_at"]) < timedelta(days=config.UNIVERSE_CACHE_TTL_DAYS)
    except Exception:
        return False


def _fetch(min_mcap: float) -> list[dict]:
    import yfinance as yf

    q = yf.EquityQuery("and", [yf.EquityQuery("eq", ["region", "jp"]),
                               yf.EquityQuery("gt", ["intradaymarketcap", float(min_mcap)])])
    rows: list[dict] = []
    offset = 0
    while True:
        last = None
        for attempt in range(3):
            try:
                r = yf.screen(q, size=250, offset=offset, sortField="intradaymarketcap", sortAsc=False)
                break
            except Exception as e:
                last = e
                time.sleep(1.5 * (attempt + 1))
        else:
            log.warning("スクリーナー取得失敗 offset=%d: %s", offset, last)
            break
        quotes = r.get("quotes") or []
        rows.extend(quotes)
        total = r.get("total") or 0
        if not quotes or len(rows) >= total or offset >= 3000:
            break
        offset += 250
    out = []
    for x in rows:
        sym = str(x.get("symbol") or "")
        if not sym.endswith(".T"):
            continue
        price = x.get("regularMarketPrice")
        vol = x.get("averageDailyVolume3Month")
        out.append({
            "symbol": sym,
            "name_en": (x.get("shortName") or x.get("longName") or sym).strip(),
            "market_cap": x.get("marketCap"),
            "price": price,
            "avg_volume_3m": vol,
            "turnover": (float(price) * float(vol)) if price and vol else None,
            "fifty_two_week_change": x.get("fiftyTwoWeekChangePercent"),
            "eps_forward": x.get("epsForward"),
            "eps_ttm": x.get("epsTrailingTwelveMonths"),
        })
    return out


def load_universe(force: bool = False) -> list[dict]:
    """流動性フィルタ後の銘柄リスト（時価総額降順・上限 UNIVERSE_MAX）。"""
    if not force and _fresh():
        data = json.loads(UNIVERSE_CACHE.read_text())
        rows = data["rows"]
    else:
        log.info("Yahoo スクリーナーで東証銘柄を取得中（時価総額 ≥ %.0f 億円）", config.UNIVERSE_MCAP_MIN / 1e8)
        rows = _fetch(config.UNIVERSE_MCAP_MIN)
        if rows:
            UNIVERSE_CACHE.write_text(json.dumps({"fetched_at": datetime.now().isoformat(), "rows": rows}, ensure_ascii=False))
    liquid = [r for r in rows if (r.get("turnover") or 0) >= config.UNIVERSE_MIN_TURNOVER]
    liquid.sort(key=lambda r: -(r.get("market_cap") or 0))
    return liquid[: config.UNIVERSE_MAX]


def universe_meta() -> dict:
    try:
        d = json.loads(UNIVERSE_CACHE.read_text())
        return {"fetched_at": d["fetched_at"], "n_raw": len(d["rows"])}
    except Exception:
        return {}
