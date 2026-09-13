"""決算説明テキスト（説明会 Q&A 書き起こし・決算短信の定性的情報・MD&A）のローカル保管。

配置: data/transcripts/<symbol>/<YYYY-MM-DD>.txt
  - <symbol> は "6902.T" 形式（"6902" だけでも可、読み込み時に補完）
  - <YYYY-MM-DD> は決算発表日（テキストが対応するイベント日）
UI からの貼り付けも同じ場所に保存される。
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from .. import config

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _norm_symbol(s: str) -> str:
    s = s.strip().upper()
    if re.fullmatch(r"\d{4}", s):
        return f"{s}.T"
    return s


def save_transcript(symbol: str, event_date: str, text: str, kind: str = "manual") -> Path:
    symbol = _norm_symbol(symbol)
    d = _DATE_RE.search(event_date)
    if not d:
        raise ValueError("日付は YYYY-MM-DD 形式で指定してください")
    folder = config.TRANSCRIPT_DIR / symbol
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{d.group(1)}.txt"
    header = f"# kind: {kind}\n# saved: {date.today().isoformat()}\n"
    path.write_text(header + text.strip() + "\n", encoding="utf-8")
    return path


def load_transcripts() -> dict[str, list[dict]]:
    """{symbol: [{date, text, kind, path}, ...]}（日付昇順）。"""
    out: dict[str, list[dict]] = {}
    root = config.TRANSCRIPT_DIR
    if not root.exists():
        return out
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        sym = _norm_symbol(folder.name)
        items = []
        for f in sorted(folder.glob("*.txt")):
            m = _DATE_RE.search(f.stem)
            if not m:
                continue
            raw = f.read_text(encoding="utf-8", errors="ignore")
            kind = "manual"
            body_lines = []
            for line in raw.splitlines():
                if line.startswith("# kind:"):
                    kind = line.split(":", 1)[1].strip()
                elif line.startswith("# saved:"):
                    continue
                else:
                    body_lines.append(line)
            body = "\n".join(body_lines).strip()
            if len(body) < 40:
                continue
            items.append({"date": m.group(1), "text": body, "kind": kind, "path": str(f)})
        if items:
            out[sym] = items
    return out


def count_transcripts() -> int:
    return sum(len(v) for v in load_transcripts().values())
