"""EDINET API v2 クライアント（金融庁・無料）。

用途:
1. 有価証券報告書の【主要な顧客ごとの情報】から supplier → customer エッジと売上依存度を抽出し、
   seed エッジを実測値で上書きする（Cohen & Frazzini の Compustat segment-customer file に相当）。
2. 有報・半期報告書の MD&A（経営者による財政状態、経営成績及びキャッシュ・フローの状況の分析）
   本文を、決算説明テキストの代替として取得する（SUE.txt の入力）。

API キーは環境変数 EDINET_FSA_KEY または data/edinet_key_fsa.txt（1 行）に置く。
キーが無ければ本モジュールは静かに何もしない（seed エッジのみで動作）。
"""
from __future__ import annotations

import html
import io
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path

from .. import config
from ..universe import CUSTOMER_ALIASES, NODE_BY_SYMBOL

log = logging.getLogger(__name__)

DOC_TYPE_ANNUAL = "120"
DOC_TYPE_SEMI = "160"
EDINET_CACHE = config.CACHE_DIR / "edinet"
EDINET_CACHE.mkdir(exist_ok=True)

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"[\s　]+")


def read_key() -> str | None:
    env = os.environ.get(config.EDINET_KEY_ENV, "").strip()
    if env:
        return env
    for p in config.EDINET_KEY_FILES:
        try:
            k = Path(p).read_text(encoding="utf-8").strip()
            if k:
                return k
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return None


def _fetch(url: str, timeout: int = 60, retries: int = 2) -> bytes:
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ripple-drift/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return res.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise PermissionError("EDINET API キーが認証されませんでした (401/403)")
            if e.code == 404:
                raise
            last = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"EDINET 通信失敗: {url} ({last})")


def list_documents(key: str, d: date) -> list[dict]:
    url = f"{config.EDINET_BASE}/documents.json?date={d.isoformat()}&type=2&Subscription-Key={urllib.parse.quote(key)}"
    cache = EDINET_CACHE / f"list_{d.isoformat()}.json"
    if cache.exists() and d < date.today():
        return json.loads(cache.read_text()).get("results") or []
    data = json.loads(_fetch(url).decode("utf-8"))
    cache.write_text(json.dumps(data, ensure_ascii=False))
    return data.get("results") or []


def document_csv_text(key: str, doc_id: str) -> str:
    """CSV(XBRL) zip を取得し、要素ごとの (要素ID, 値) を行としたテキストへ整形する。"""
    cache = EDINET_CACHE / f"{doc_id}.txt"
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    url = f"{config.EDINET_BASE}/documents/{doc_id}?type=5&Subscription-Key={urllib.parse.quote(key)}"
    try:
        raw = _fetch(url)
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except Exception as e:
        log.warning("EDINET 書類取得失敗 %s: %s", doc_id, e)
        return ""
    parts = []
    for name in zf.namelist():
        if name.lower().endswith(".csv"):
            try:
                parts.append(zf.read(name).decode("utf-16", errors="ignore"))
            except Exception:
                continue
    text = "\n".join(parts)
    cache.write_text(text, encoding="utf-8")
    return text


def sec_code_to_symbol(sec_code: str | None) -> str | None:
    """EDINET の secCode（5 桁, 末尾 0）→ yfinance シンボル。"""
    if not sec_code:
        return None
    s = str(sec_code).strip()
    if len(s) == 5 and s.endswith("0"):
        s = s[:4]
    return f"{s}.T"


# ---- 主要な顧客の抽出 ----


def _clean(s: str) -> str:
    s = html.unescape(TAG_RE.sub(" ", s))
    return WS_RE.sub(" ", s).strip()


def normalize_customer_name(name: str) -> str:
    name = name.replace("株式会社", "").replace("㈱", "").replace("(株)", "").replace("（株）", "")
    name = re.sub(r"[\s　]+", "", name)
    return name.strip()


def resolve_customer(name: str) -> str | None:
    n = normalize_customer_name(name)
    for alias, sym in CUSTOMER_ALIASES.items():
        a = normalize_customer_name(alias)
        if a and (a == n or a in n or n in a and len(n) >= 3):
            return sym
    return None


_AMOUNT_RE = re.compile(r"([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,})")
_MAIN_CUSTOMER_ELEM = "InformationForEachOfMainCustomersTextBlock"
_REVENUE_ELEM_RE = re.compile(r'^"?jpcrp_cor:(?:NetSales|Revenue|Revenues|OperatingRevenue|SalesRevenue)[A-Za-z]*SummaryOfBusinessResults"?$')


def _csv_rows(csv_text: str):
    """タブ区切り行 → (要素ID, 項目名, コンテキストID, 値) のタプル。"""
    for line in csv_text.splitlines():
        cols = [c.strip().strip('"') for c in line.split("\t")]
        if len(cols) < 4:
            continue
        yield cols[0], cols[1], cols[2], cols[-1]


def extract_total_revenue(csv_text: str) -> float | None:
    """当期・連結の売上高（円）。主要な経営指標等の売上高（IFRS/米国基準の売上収益を含む）から取る。"""
    best = None
    for elem, _name, ctx, val in _csv_rows(csv_text):
        if ctx != "CurrentYearDuration" or not _REVENUE_ELEM_RE.match(elem):
            continue
        try:
            v = float(val.replace(",", ""))
        except ValueError:
            continue
        if v > 0 and (best is None or v > best):
            best = v
    return best


def _unit_multiplier(text: str) -> float:
    if "百万円" in text:
        return 1e6
    if "千円" in text:
        return 1e3
    if "億円" in text:
        return 1e8
    return 1e6


_ALIASES_SORTED = sorted(CUSTOMER_ALIASES.items(), key=lambda kv: -len(kv[0]))


def _find_customers_in_block(block: str) -> list[dict]:
    """名寄せ辞書に載っている顧客名を本文中から探し、直後に現れる金額を売上高とみなす。

    EDINET の CSV では表のセルが区切り無しで連結される（例: "Intel Corporation59,210検査・測定装置事業"）
    ため、トークン分割ではなく既知の名称を直接検索する。
    """
    unit = _unit_multiplier(block)
    found: dict[str, dict] = {}
    for alias, sym in _ALIASES_SORTED:
        if not alias or sym in found:
            continue
        flags = re.IGNORECASE if re.fullmatch(r"[A-Za-z0-9 .,&\-]+", alias) else 0
        for m in re.finditer(re.escape(alias), block, flags):
            tail = block[m.end(): m.end() + 80]
            am = _AMOUNT_RE.search(tail)
            if not am:
                continue
            amount = float(am.group(1).replace(",", "")) * unit
            if amount <= 0:
                continue
            found[sym] = {"customer_name": normalize_customer_name(alias), "customer_symbol": sym, "amount": amount}
            break
    return list(found.values())


def extract_major_customers(csv_text: str) -> list[dict]:
    """有報【主要な顧客ごとの情報】から (顧客, 売上高[円], 売上高比率) を抽出する。

    1. 専用要素 jpcrp_cor:InformationForEachOfMainCustomersTextBlock の当期（CurrentYearDuration）を優先。
    2. 無ければセグメント情報等の注記テキストの、最後に現れる「主要な顧客ごとの情報」以降を使う。
    比率は当期連結売上高（NetSalesSummaryOfBusinessResults 等）で割る。
    """
    block = ""
    fallback = ""
    for elem, _name, ctx, val in _csv_rows(csv_text):
        if _MAIN_CUSTOMER_ELEM in elem:
            if ctx == "CurrentYearDuration":
                block = _clean(val)
                break
            if not fallback:
                fallback = _clean(val)
        elif "主要な顧客ごとの情報" in val and "SegmentInformation" in elem and not fallback:
            t = _clean(val)
            i = t.rfind("主要な顧客ごとの情報")
            fallback = t[i:i + 2000]
    block = block or fallback
    if not block:
        return []
    total = extract_total_revenue(csv_text)
    out = _find_customers_in_block(block)
    for c in out:
        c["share"] = (c["amount"] / total) if total and total > 0 else None
    return out


# ---- MD&A テキスト抽出 ----
_MDA_KEYS = ("経営者による財政状態", "経営成績及びキャッシュ・フローの状況の分析", "経営成績等の状況の概要")


def extract_mda_text(csv_text: str, max_chars: int = 12000) -> str:
    best = ""
    for line in csv_text.splitlines():
        if any(k in line for k in _MDA_KEYS):
            t = _clean(line)
            if len(t) > len(best):
                best = t
    return best[:max_chars]


# ---- 日本語社名（提出者リスト） ----
def filer_name_map() -> dict[str, str]:
    """キャッシュ済みの書類一覧（documents.json）から secCode → 提出者名（日本語）を作る。"""
    names: dict[str, str] = {}
    for f in sorted(EDINET_CACHE.glob("list_*.json")):
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue
        for d in data.get("results") or []:
            sc, fn = d.get("secCode"), d.get("filerName")
            if sc and fn and len(str(sc)) == 5:
                names[f"{str(sc)[:4]}.T"] = fn
    return names


def prefetch_lists(days_back: int = 400) -> int:
    """書類一覧だけを取得（社名マップ用）。キーが無ければ 0。"""
    key = read_key()
    if not key:
        return 0
    n = 0
    today = date.today()
    for i in range(days_back):
        d = today - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        try:
            list_documents(key, d)
            n += 1
        except Exception:
            continue
    return n


# ---- 一括処理 ----
def refresh_from_edinet(days_back: int = 400, symbols: set[str] | None = None,
                        progress=None) -> dict:
    """直近 days_back 日に提出された有報・半期報告書を走査し、
    (1) 主要顧客エッジ (2) MD&A テキストを収集して返す。キーが無ければ空。"""
    key = read_key()
    result = {"enabled": False, "edges": [], "texts": {}, "docs_scanned": 0, "errors": []}
    if not key:
        return result
    result["enabled"] = True
    targets = symbols or set(NODE_BY_SYMBOL)
    today = date.today()
    scanned = 0
    for i in range(days_back):
        d = today - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        try:
            docs = list_documents(key, d)
        except PermissionError as e:
            result["errors"].append(str(e))
            break
        except Exception as e:
            result["errors"].append(f"{d}: {e}")
            continue
        for doc in docs:
            if doc.get("docTypeCode") not in (DOC_TYPE_ANNUAL, DOC_TYPE_SEMI):
                continue
            sym = sec_code_to_symbol(doc.get("secCode"))
            if sym not in targets:
                continue
            txt = document_csv_text(key, doc["docID"])
            if not txt:
                continue
            scanned += 1
            if progress:
                progress(scanned, sym)
            if doc.get("docTypeCode") == DOC_TYPE_ANNUAL:
                for c in extract_major_customers(txt):
                    if c["customer_symbol"] and c["customer_symbol"] != sym:
                        result["edges"].append({
                            "supplier": sym, "customer": c["customer_symbol"],
                            "share": c["share"], "amount": c["amount"],
                            "doc_id": doc["docID"], "submitted": doc.get("submitDateTime"),
                            "customer_name": c["customer_name"],
                        })
            mda = extract_mda_text(txt)
            if mda:
                sub = (doc.get("submitDateTime") or d.isoformat())[:10]
                result["texts"].setdefault(sym, []).append({"date": sub, "doc_id": doc["docID"], "text": mda,
                                                            "kind": "有報MD&A" if doc.get("docTypeCode") == DOC_TYPE_ANNUAL else "半期報告書MD&A"})
    result["docs_scanned"] = scanned
    return result
