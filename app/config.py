"""グローバル設定。パスと定量パラメータをここに集約する。

パラメータの出典:
- PEAD の保有期間 60 営業日、ドリフト計測窓 [+2, +60]: Meursault et al. (2023) の主要窓に準拠。
- 顧客モメンタムの形成期間 1 か月: Cohen & Frazzini (2008) Table II の基準ソート。
- 上位 5% スクリーン: ユーザー要件。
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
TRANSCRIPT_DIR = DATA_DIR / "transcripts"
OUTPUT_DIR = ROOT / "output"
RESULTS_PATH = OUTPUT_DIR / "results.json"
WEB_DIR = ROOT / "web"

for _d in (CACHE_DIR, TRANSCRIPT_DIR, OUTPUT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---- データ取得 ----
PRICE_START = "2018-01-01"          # 日次株価の取得開始日
EARNINGS_LIMIT = 48                 # 決算イベント取得件数（四半期 × 12 年分の上限）
PRICE_CACHE_TTL_HOURS = 12
EARNINGS_CACHE_TTL_HOURS = int(os.environ.get("RIPPLE_EARNINGS_TTL_HOURS", "24"))
INFO_CACHE_TTL_DAYS = 14
JP_BENCHMARK = "1306.T"             # TOPIX 連動 ETF（市場調整リターンの基準）
JP_INDEX_FALLBACK = "^N225"
US_BENCHMARK = "^GSPC"
KR_BENCHMARK = "^KS11"
TW_BENCHMARK = "^TWII"

# ---- ユニバース ----
UNIVERSE_MODE = os.environ.get("RIPPLE_UNIVERSE", "broad")          # broad | curated
UNIVERSE_MCAP_MIN = float(os.environ.get("RIPPLE_MCAP_MIN", "3e10"))  # 時価総額 300 億円以上
UNIVERSE_MIN_TURNOVER = float(os.environ.get("RIPPLE_MIN_TURNOVER", "1e8"))  # 売買代金 1 億円/日以上
UNIVERSE_MAX = int(os.environ.get("RIPPLE_UNIVERSE_MAX", "1500"))
UNIVERSE_CACHE_TTL_DAYS = 7
EDINET_ALL = os.environ.get("RIPPLE_EDINET_ALL", "0") == "1"        # 1 なら全銘柄の有報を取得（初回は非常に遅い）
SPARK_TOP_N = 80                                                     # スパークライン・CAR 経路を付ける上位件数
VOL_DAYS = 60

# ---- TDnet（決算短信の定性的情報） ----
TDNET_ENABLED = os.environ.get("RIPPLE_NO_TDNET", "0") != "1"
TDNET_DAYS_BACK = int(os.environ.get("RIPPLE_TDNET_DAYS", "40"))

# ---- イベントスタディ（PEAD） ----
CAR_START_OFFSET = 2                # 発表日 t0 からの開始オフセット（営業日）
CAR_HORIZON = 60                    # ドリフト計測期間（営業日）
CAR_SHORT_HORIZON = 20
MIN_CELL_N = 20                     # 条件表セルの最小サンプル数
SRW_WINDOW = 8                      # 季節ランダムウォークの分散推定に使う四半期数
SIGNAL_DECAY_DAYS = CAR_HORIZON     # 直近決算シグナルの鮮度（この営業日数で 0 に減衰）

# ---- サプライチェーン ----
LEADLAG_MAX_LAG_MONTHS = 6
LEADLAG_MAX_LAG_WEEKS = 12
CM_FORMATION_DAYS = 21              # 顧客モメンタム形成期間（≈1 か月）
CM_FORMATION_DAYS_LONG = 63         # 同 3 か月
MOM_SKIP_MONTHS = 1                 # 自社モメンタム（t-12..t-2）のスキップ
MOM_LOOKBACK_MONTHS = 11

# ---- スクリーン ----
TOP_FRACTION = 0.05                 # 上位 5%
MIN_CANDIDATES = 5
RING_CLOSE_THRESHOLD = 70           # リングが「達成」とみなされるスコア
ALL_RINGS_BONUS = 8                 # 3 リング全達成ボーナス（合成スコア加点）
NEUTRAL_SCORE = 50.0

# ---- テキスト ----
TEXT_MODEL_BACKEND = os.environ.get("RIPPLE_TEXT_BACKEND", "auto")   # auto | lexicon | finbert
FINBERT_MODEL = os.environ.get("RIPPLE_FINBERT_MODEL", "ProsusAI/finbert")
MIN_LABELED_FOR_LOGIT = 30

# ---- EDINET ----
EDINET_BASE = "https://api.edinet-fsa.go.jp/api/v2"
EDINET_KEY_ENV = "EDINET_FSA_KEY"
EDINET_KEY_FILES = [
    DATA_DIR / "edinet_key_fsa.txt",
    Path.home() / "cre-scout" / "tools" / "edinet_key_fsa.txt",
]

# ---- サーバ ----
HOST = os.environ.get("RIPPLE_HOST", "127.0.0.1")
PORT = int(os.environ.get("RIPPLE_PORT", "8765"))
