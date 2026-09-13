"""TDnet（東証 適時開示情報閲覧サービス）から決算短信の定性的情報を取得する。

- 日次一覧: https://www.release.tdnet.info/inbs/I_list_{page:03d}_{YYYYMMDD}.html（100 件/ページ）
- 決算短信の XBRL zip には XBRLData/Attachment/qualitative.htm（経営成績等の概況・今後の見通し）が入る。
- 公開されているのは直近 40 日程度。毎日の自動更新で取り込み、data/transcripts/ に蓄積する。

取得した本文は「決算説明テキスト」として SUE.txt（経営陣の自信）の入力になる。
TDnet は公的な開示サービスなので、間隔を空けて礼儀正しく取得する（1 リクエスト 0.25 秒以上）。
"""
from __future__ import annotations

import html
import io
import logging
import re
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path

from .. import config
from . import transcripts

log = logging.getLogger(__name__)

BASE = "https://www.release.tdnet.info/inbs/"
CACHE = config.CACHE_DIR / "tdnet"
CACHE.mkdir(exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (compatible; ripple-drift/1.0; research use)"}
POLITE_SLEEP = 0.25

_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CODE_RE = re.compile(r'kjCode[^>]*>\s*([0-9A-Z]{4,5})')
_NAME_RE = re.compile(r'kjName[^>]*>\s*(.*?)\s*<', re.S)
_TITLE_RE = re.compile(r'kjTitle[^>]*>\s*<a href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_XBRL_RE = re.compile(r'kjXbrl[^>]*>.*?href="([^"]+\.zip)"', re.S)
_TIME_RE = re.compile(r'kjTime[^>]*>\s*(\d{1,2}:\d{2})')
_TOTAL_RE = re.compile(r"全(\d+)件")
_TAG_RE = re.compile(r"<[^>]+>")
_STYLE_RE = re.compile(r"<(style|script)[^>]*>.*?</\1>", re.S | re.I)

SKIP_WORDS = ("訂正", "修正", "補足", "説明資料", "参考資料", "英文", "English")


def code_to_symbol(code: str) -> str:
    """TDnet の 5 桁コード（末尾 0、英字入りあり）→ yfinance シンボル。"""
    c = code.strip()
    if len(c) == 5 and c.endswith("0"):
        c = c[:4]
    return f"{c}.T"


def _fetch(url: str, timeout: int = 40) -> bytes | None:
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                data = r.read()
            time.sleep(POLITE_SLEEP)
            return data
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(1.5 * (attempt + 1))
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return None


def list_page(d: date, page: int) -> str | None:
    """一覧ページの HTML（過去日はキャッシュ、当日は毎回取得）。404 なら None。"""
    cache = CACHE / f"list_{d:%Y%m%d}_{page:03d}.html"
    if cache.exists() and d < date.today():
        return cache.read_text(encoding="utf-8")
    raw = _fetch(f"{BASE}I_list_{page:03d}_{d:%Y%m%d}.html")
    if raw is None:
        return None
    text = raw.decode("utf-8", errors="ignore")
    if d < date.today():
        cache.write_text(text, encoding="utf-8")
    return text


def parse_rows(page_html: str) -> list[dict]:
    out = []
    for row in _ROW_RE.findall(page_html):
        if "kjTitle" not in row:
            continue
        t = _TITLE_RE.search(row)
        c = _CODE_RE.search(row)
        if not t or not c:
            continue
        x = _XBRL_RE.search(row)
        n = _NAME_RE.search(row)
        tm = _TIME_RE.search(row)
        out.append({
            "code": c.group(1), "symbol": code_to_symbol(c.group(1)),
            "name": html.unescape(n.group(1)).strip() if n else "",
            "title": html.unescape(re.sub(r"\s+", " ", t.group(2))).strip(),
            "pdf": t.group(1), "xbrl": x.group(1) if x else None, "time": tm.group(1) if tm else None,
        })
    return out


def page_total(page_html: str) -> int:
    m = _TOTAL_RE.search(page_html)
    return int(m.group(1)) if m else 0


def is_tanshin(title: str) -> bool:
    return "決算短信" in title and not any(w in title for w in SKIP_WORDS)


def clean_html_text(raw: str) -> str:
    t = _STYLE_RE.sub(" ", raw)
    t = _TAG_RE.sub(" ", t)
    t = html.unescape(t)
    t = re.sub(r"[\s　]+", " ", t).strip()
    return t


def extract_qualitative(zip_bytes: bytes, max_chars: int = 12000) -> str:
    """XBRL zip から定性的情報の本文を取り出す。qualitative.htm が無ければ経営成績を含む添付を探す。"""
    try:
        z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        return ""
    names = z.namelist()
    target = next((n for n in names if n.lower().endswith("qualitative.htm")), None)
    text = ""
    if target:
        text = clean_html_text(z.read(target).decode("utf-8", errors="ignore"))
    if len(text) < 200:
        for n in names:
            if not n.lower().endswith((".htm", ".html")) or "/Summary/" in n:
                continue
            t = clean_html_text(z.read(n).decode("utf-8", errors="ignore"))
            if ("経営成績" in t or "業績" in t) and len(t) > len(text):
                text = t
    # 目次（見出し + 点線 + ページ番号）は最初に出るので、本文側＝最後の出現位置から取る
    text = re.sub(r"[…‥]{2,}\s*\d*", " ", text)
    start = 0
    for key in ("定性的情報", "経営成績等の概況", "経営成績に関する説明", "経営成績等の状況"):
        i = text.rfind(key)
        if i >= 0 and len(text) - i > 200:
            start = i
            break
    end = len(text)
    for key in ("四半期連結財務諸表及び主な注記", "連結財務諸表及び主な注記", "四半期財務諸表及び主な注記", "財務諸表及び主な注記",
                "中間連結財務諸表及び主な注記", "要約四半期連結財務諸表"):
        j = text.find(key, start + 50)
        if j > 0:
            end = min(end, j)
    return text[start:end][:max_chars]


def refresh_tdnet(symbols: set[str], days_back: int = 40, progress=None) -> dict:
    """直近 days_back 日の決算短信をユニバース分だけ取り込む。返り値は統計。"""
    stats = {"dates": 0, "pages": 0, "rows": 0, "matched": 0, "saved": 0, "skipped_existing": 0, "no_text": 0, "errors": 0}
    existing = transcripts.load_transcripts()
    today = date.today()
    misses = 0
    for i in range(days_back + 1):
        d = today - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        first = list_page(d, 1)
        if first is None:
            misses += 1
            if misses >= 3 and i > 7:      # 保存期間の終端（連続で 404）
                break
            continue
        misses = 0
        stats["dates"] += 1
        total = page_total(first)
        n_pages = max(1, (total + 99) // 100)
        pages = [first]
        for p in range(2, n_pages + 1):
            h = list_page(d, p)
            if h is None:
                break
            pages.append(h)
        stats["pages"] += len(pages)
        for h in pages:
            for r in parse_rows(h):
                stats["rows"] += 1
                if r["symbol"] not in symbols or not is_tanshin(r["title"]) or not r["xbrl"]:
                    continue
                stats["matched"] += 1
                ds = d.isoformat()
                if any(t["date"] == ds for t in existing.get(r["symbol"], [])):
                    stats["skipped_existing"] += 1
                    continue
                zcache = CACHE / r["xbrl"]
                if zcache.exists():
                    zb = zcache.read_bytes()
                else:
                    zb = _fetch(BASE + r["xbrl"])
                    if zb is None:
                        stats["errors"] += 1
                        continue
                    zcache.write_bytes(zb)
                text = extract_qualitative(zb)
                if len(text) < 200:
                    stats["no_text"] += 1
                    continue
                header = f"{r['title']}（{r['name']}、TDnet {ds}）\n"
                transcripts.save_transcript(r["symbol"], ds, header + text, kind="決算短信")
                existing.setdefault(r["symbol"], []).append({"date": ds, "text": text, "kind": "決算短信", "path": ""})
                stats["saved"] += 1
                if progress:
                    progress(stats["saved"], r["symbol"], ds)
    return stats
