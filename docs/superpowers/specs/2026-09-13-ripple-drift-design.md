# RippleDrift 設計書（2026-09-13）

> 決定事項の記録。ユーザー不在のため、以下の判断は実装者（Claude）が行った。
> 変更したい点があれば、この文書を直してから再実装を依頼すれば良い。

## 1. 目的

2つの学術アノマリーを合成し、日本株の「スイングロング（60営業日以内の値上がり狙い）」候補を
初心者でも読める形で提示するローカル・アプリケーション。

| # | アノマリー | 論文 | 本アプリでの実装名 |
|---|---|---|---|
| 1 | サプライチェーン・モメンタム（customer momentum） | Cohen & Frazzini (2008), *Economic Links and Predictable Returns*, JF 63(4) | **取引先の追い風**（Ring C） |
| 2 | PEAD × 開示テキスト（SUE.txt） | Meursault, Liang, Routledge & Scanlon (2023), *PEAD.txt: Post-Earnings-Announcement Drift Using Text*, JFQA | **決算サプライズ**（Ring A）＋**経営陣の自信**（Ring B） |

理論・コードは専門用語で厳密に、UI は平易な日本語で、という二層構造をとる。

## 2. 前提と制約（自己決定）

- **対象市場**: 日本株（東証プライム中心、時価総額上位の約 120 銘柄を curated universe として同梱）。
  サプライチェーンの「顧客」ノードとして海外企業（Apple, TSMC, NVIDIA, Intel, Samsung 等）も含める。
- **データ源**: すべて無料。
  - 株価・EPS 予想/実績・決算発表日: Yahoo Finance（`yfinance`）。
  - 主要な顧客（有報【主要な顧客ごとの情報】）: 同梱の seed エッジ表 ＋ EDINET API v2（キーがある場合に自動補強）。
  - 決算説明テキスト: ユーザーが貼り付け（`data/transcripts/<code>/<date>.txt`）。EDINET キーがあれば
    有報・半期報告書の MD&A（経営者による分析）本文を代替テキストとして取得可能。
- **FinBERT**: 英語テキストには `ProsusAI/finbert`（`transformers`、任意インストール）。
  日本語テキストには金融極性辞書（Loughran–McDonald を日本語化した独自辞書）＋確信度マーカーの
  ルールベース、および十分なラベル付きイベントが溜まった時点でロジスティック回帰に切り替える
  SUE.txt 推定器を実装する。モデル名の捏造を避けるため、日本語 BERT は設定で差し替え可能とし既定では使わない。
- **一回で動く**: `./run.sh` の一発で仮想環境作成 → データ取得 → 計算 → ブラウザ起動まで行う。
- **投資助言ではない**: UI に免責を常設。数値はすべて過去データの統計であり将来の保証ではない。

## 3. アーキテクチャ

```
ripple-drift/
├── run.sh                    # 一発起動（venv・依存・パイプライン・サーバ・ブラウザ）
├── app/
│   ├── config.py             # パス・パラメータ（イベント窓、リング閾値、上位5%）
│   ├── universe.py           # 銘柄ユニバース・サプライチェーン seed エッジ・名寄せ辞書
│   ├── data/prices.py        # yfinance 一括取得＋キャッシュ、市場別カレンダー処理
│   ├── data/fundamentals.py  # 決算発表日・EPS予想/実績（earnings_dates）＋キャッシュ
│   ├── data/edinet.py        # EDINET API v2 クライアント＋「主要な顧客」「MD&A」抽出
│   ├── data/transcripts.py   # ローカル・テキストの読み書き
│   ├── models/sue.py         # SUE（アナリスト基準）／SUE_SRW（季節ランダムウォーク）
│   ├── models/text.py        # 金融極性辞書・確信度・FinBERT・SUE.txt 推定器
│   ├── models/supply_chain.py# NetworkX 有向グラフ、顧客モメンタム、交差自己共分散、Fama–MacBeth
│   ├── models/pead.py        # イベントスタディ（CAR）、条件別ドリフト表、上位5%条件の探索
│   ├── models/screen.py      # 3リング・スコア、合成、上位5%抽出、平易な説明文生成
│   ├── pipeline.py           # 上記を順に実行し output/results.json を書く
│   └── server.py             # FastAPI: 静的UI配信・結果API・更新ジョブ・テキスト登録
├── web/ index.html, app.js, styles.css, echarts.min.js（evilcharts 同梱版）
├── data/ cache/, transcripts/            # 生成物（git 管理外）
├── output/results.json                    # UI が読む唯一の成果物
└── tests/                                  # 数式コアの単体テスト
```

データフロー: `pipeline.py` が唯一の書き手。UI は `results.json` を読むだけ。
サーバの「更新」ボタンはバックグラウンドで `pipeline.run()` を呼び、進捗を `/api/status` で返す。

## 4. 定量モデル

### 4.1 SUE（標準化予想外利益）
- 主: `SUE_i,q = (EPS_actual − EPS_consensus) / P_{t−1}`（Meursault et al. と同じ価格スケーリング）。
- 副: 季節ランダムウォーク `SUE^SRW = (EPS_q − EPS_{q−4} − μ) / σ`（Foster–Olsen–Shevlin 1984、直近 8 四半期）。
- 横断面で四半期ごとにランク標準化 → 十分位（decile）。

### 4.2 SUE.txt（テキスト由来サプライズ）
- 特徴量: 極性トーン `(pos−neg)/(pos+neg+1)`、確信度 `(certain−uncertain)/(…+1)`、
  前向き言及率、数値言及密度、（利用可能なら）FinBERT の positive−negative 確率差。
- 推定器: ラベル付きイベント（テキスト＋実現 SUE 符号）が 30 件以上あれば L2 ロジスティック回帰、
  未満なら事前重みによる線形合成。出力は [−1, 1]（正のサプライズ確率を中心化）。

### 4.3 サプライチェーン・モメンタム
- 有向グラフ `G`: supplier → customer、エッジ重み = 売上依存度（正規化）。
- 顧客モメンタム `CM_i,t = Σ_j w_ij · r̃_j,t`（r̃ は市場調整済み過去 1 か月／3 か月リターン）。
- リード・ラグ: 交差自己相関 `ρ_ij(k) = corr(r_i,t, r_j,t−k)`、k = 0..6（月次）と 0..12（週次）。
  エッジ加重平均の最大となる `k* ≥ 1` を「波及ラグ」と定義。
- Fama–MacBeth: `r_i,t+1 = α_t + β_t CM_i,t + γ_t r_i,t + δ_t MOM_i,(t−12,t−2) + ε`、
  時系列平均と Newey–West t 値。
- 過小反応ギャップ: `gap_i = z(CM_i) − z(r̃_i)`（顧客は動いたが本人はまだ、の度合い）。

### 4.4 PEAD イベントスタディ
- 市場調整 CAR `CAR_i[+2,+60] = Σ (r_i − r_m)`（発表日 t0 は JST 換算）。
- 十分位ポートフォリオの平均 CAR 経路（1..60 日）と、SUE 三分位 × SUE.txt 三分位の条件表。
- 「最も強いドリフトが出る条件」= 条件表の中で平均 CAR60 が最大かつ n ≥ 20 のセル。

### 4.5 合成スクリーン（上位 5%）
- Ring A = 直近イベント SUE の横断面パーセンタイル × 鮮度減衰（60 営業日で 0）。
- Ring B = SUE.txt を [0,100] に写像（テキスト無しは 50・要登録フラグ）。
- Ring C = gap の横断面パーセンタイル（顧客エッジ無しは 50・フラグ）。
- 合成 = 利用可能リングの平均 ＋ 3 リング全達成ボーナス。上位 5% を候補として提示。

## 5. UI（初心者向け）

参考画像（モノクロの分析画面）と `~/.claude/design-system.md` を融合。
- 単一カラム、最大幅 760px、ニュートラル背景、薄い区切り線、小さな大文字セクションラベル、大きな数字。
- 3 つの「リング」（決算サプライズ／経営陣の自信／取引先の追い風）で銘柄の状態を直感表示。
- 各チャートの直下に平易な一文キャプション（参考画像の流儀）。
- 色は無彩色ベース＋青 1 色のみ（主要操作・選択）。状態は色だけで伝えない（数値・ラベル併記）。
- チャートは evilcharts 仕様（ECharts 同梱、線幅 0.8、破線グリッド、HTML ツールチップ）。
- 「もっと詳しく（専門家向け）」の折りたたみに数式と論文の要旨。

## 6. テスト
- SUE 計算、季節ランダムウォーク、極性辞書スコア、交差自己相関のラグ検出、リング合成、
  EDINET「主要な顧客」抽出（合成 CSV フィクスチャ）を pytest で検証。
- パイプラインは実データで 1 回通し、UI はスクリーンショットで検品（上端・スクロール後）。

## 7. 実装順序
1. universe / config → 2. prices / fundamentals（キャッシュ）→ 3. sue / text / supply_chain / pead（テスト同時）
→ 4. screen / pipeline（results.json）→ 5. server → 6. web（UI）→ 7. run.sh / README → 8. 通し検証。


## 8. 追記（2026-09-13 夜、ユーザー要望による変更）

- **ユニバース拡張**: 同梱 seed（約 120）＋ Yahoo Finance スクリーナー（region=jp、時価総額 ≥ 300 億円、売買代金 ≥ 1 億円/日、上限 1,500）。日本語社名は EDINET 提出者リスト（documents.json の filerName）から。EDINET の有報取得は seed のみ（`RIPPLE_EDINET_ALL=1` で全銘柄）。
- **値動き指標**: 60 日年率ボラ・三分位ラベル（大/中/小）、損切り目安 clamp(2.2·σ20·√10, 5%, 15%)。UI に「値動き小を除く」トグル。
- **ひとつ買うなら**: 候補内で (all_closed, 点灯数, A 点灯, 45 営業日以内, 合成, ボラ) の辞書順で 1 銘柄を決定。判定 3 段階。
- **UI**: finance 風（参考画像）に改装。最上部に状態連動の「今やること」5 ステップ → ひとつ買うなら → ルール → 候補。
- **EDINET パーサー**: 実 CSV はセルが連結されるため、名寄せ辞書の名称検索＋直後の金額で抽出。売上高比率は NetSalesSummaryOfBusinessResults（当期・連結）で除算。
