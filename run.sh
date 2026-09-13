#!/usr/bin/env bash
# RippleDrift 一発起動: 仮想環境 → 依存 → 初回計算 → サーバ → ブラウザ
set -euo pipefail
cd "$(dirname "$0")"

PORT="${RIPPLE_PORT:-8765}"
if [ ! -d .venv ]; then
  echo "▶ Python 仮想環境を作成します (.venv)"
  if command -v uv >/dev/null 2>&1; then uv venv --python 3.12 .venv >/dev/null; else python3 -m venv .venv; fi
fi
# shellcheck disable=SC1091
source .venv/bin/activate
if ! python -c "import yfinance, fastapi, networkx, statsmodels" >/dev/null 2>&1; then
  echo "▶ 依存ライブラリをインストールします（初回のみ・1〜2分）"
  if command -v uv >/dev/null 2>&1; then uv pip install -r requirements.txt >/dev/null; else pip install -q -r requirements.txt; fi
fi
if [ ! -f output/results.json ] || [ "${1:-}" = "--refresh" ]; then
  echo "▶ 市場データを取得して計算します（初回は約1〜2分）"
  python -m app.pipeline ${RIPPLE_NO_EDINET:+--no-edinet}
fi
echo "▶ http://127.0.0.1:${PORT} でサーバを起動します（終了は Ctrl+C）"
( sleep 1.5; open "http://127.0.0.1:${PORT}" >/dev/null 2>&1 || true ) &
exec python -m app.server
