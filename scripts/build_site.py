"""GitHub Pages 用の静的サイトを site/ に組み立てる。

構成（ローカルの FastAPI 配信と同じ相対パス）:
  site/index.html
  site/static/{app.js, styles.css, echarts.min.js}
  site/results.json
  site/.nojekyll
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
OUT = ROOT / "site"


def main() -> int:
    results = ROOT / "output" / "results.json"
    if not results.exists():
        print("output/results.json がありません。先に python -m app.pipeline を実行してください。", file=sys.stderr)
        return 1
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "static").mkdir(parents=True)
    shutil.copy2(WEB / "index.html", OUT / "index.html")
    for name in ("app.js", "styles.css", "echarts.min.js"):
        shutil.copy2(WEB / name, OUT / "static" / name)
    shutil.copy2(results, OUT / "results.json")
    (OUT / ".nojekyll").write_text("")
    size = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    print(f"site/ を作成しました（{size / 1e6:.1f} MB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
