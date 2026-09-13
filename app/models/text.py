"""開示テキストからのサプライズ推定（SUE.txt）。

Meursault, Liang, Routledge & Scanlon (2023) は、決算説明会テキストから
「利益サプライズの符号」を予測する教師ありモデルを学習し、その out-of-sample 予測確率を
中心化したものを SUE.txt と定義した。数値サプライズと独立に PEAD を生み、両者の合成で
ドリフトが最大化する、というのが主結果である。

本モジュールは同じ骨格を、依存を最小にして再現する。

  特徴量 φ(text):
    tone      = (pos − neg) / (pos + neg + 1)         … 金融極性辞書（Loughran–McDonald 型）
    certainty = (certain − uncertain) / (certain + uncertain + 1)
    forward   = 前向き表現（来期・見通し・計画）の密度
    numeric   = 数値言及密度（定量的な説明ほど確信が高い、という経験則）
    finbert   = FinBERT の P(positive) − P(negative)（英語テキストで transformers があるとき）

  推定器:
    ラベル付きイベント（テキスト + 実現 SUE の符号）が MIN_LABELED_FOR_LOGIT 件以上あれば
    L2 正則化ロジスティック回帰（Newton–Raphson）を学習し、
    SUE.txt = 2·P̂(surprise > 0) − 1 ∈ [−1, 1] とする。
    未満のときは事前重み（prior）による線形合成を tanh で [−1, 1] に押し込む。
"""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field

import numpy as np

from .. import config

log = logging.getLogger(__name__)

# ---- 日本語 金融極性辞書（Loughran–McDonald の意味カテゴリを日本語開示語彙へ写像） ----
JP_POSITIVE = [
    "増収", "増益", "過去最高", "最高益", "好調", "堅調", "上方修正", "拡大", "改善", "回復", "順調",
    "伸長", "増加", "上回", "達成", "成長", "強い需要", "旺盛", "黒字化", "増配", "力強", "加速",
    "高水準", "好転", "受注増", "上振れ", "想定を上回", "計画を上回", "最高を更新", "採算改善",
]
JP_NEGATIVE = [
    "減収", "減益", "下方修正", "減少", "悪化", "低迷", "厳しい", "低調", "下回", "赤字", "減損",
    "特別損失", "縮小", "落ち込", "弱含", "不振", "停滞", "鈍化", "減配", "下振れ", "想定を下回",
    "計画を下回", "遅れ", "値下げ圧力", "コスト増", "原材料高", "採算悪化", "在庫調整", "失速",
]
JP_CERTAIN = [
    "確実", "確信", "着実", "明確", "必ず", "計画どおり", "計画通り", "予定どおり", "予定通り",
    "見込んでおります", "達成できる", "自信", "手応え", "目途", "めど", "織り込", "既に", "実現",
]
JP_UNCERTAIN = [
    "不透明", "懸念", "可能性", "リスク", "注視", "慎重", "予断を許さない", "見通しにくい", "不確実",
    "変動", "影響を受ける", "かもしれ", "見極め", "場合によって", "不安", "未定", "精査中",
]
JP_FORWARD = [
    "来期", "次期", "今後", "見通し", "計画", "予想", "目標", "中期", "来年度", "下期", "通期",
    "見込み", "想定", "方針", "戦略",
]

# ---- 英語（Loughran–McDonald 抜粋。FinBERT 不在時のフォールバック） ----
EN_POSITIVE = [
    "strong", "growth", "record", "improve", "improved", "improvement", "exceed", "exceeded", "beat",
    "robust", "momentum", "increase", "increased", "expand", "expansion", "outperform", "confident",
    "opportunity", "profitability", "raise", "raised", "accelerat", "solid", "favorable", "upside",
]
EN_NEGATIVE = [
    "decline", "declined", "weak", "weakness", "loss", "losses", "impairment", "miss", "missed",
    "headwind", "headwinds", "challenging", "difficult", "adverse", "lower", "decrease", "decreased",
    "pressure", "slowdown", "uncertain", "restructuring", "writedown", "shortfall", "cut", "downside",
]
EN_CERTAIN = ["confident", "certain", "clearly", "definitely", "on track", "will", "committed", "expect to deliver"]
EN_UNCERTAIN = ["uncertain", "uncertainty", "may", "might", "could", "possibly", "risk", "risks", "volatile",
                "unclear", "depends", "cautious", "monitor"]
EN_FORWARD = ["outlook", "guidance", "next quarter", "next year", "full year", "expect", "forecast", "plan", "target"]

# 事前重み（ラベル付きデータが揃うまでの線形合成）。tone を主、certainty を従とする。
PRIOR_WEIGHTS = {"tone": 1.6, "certainty": 0.8, "forward": 0.3, "numeric": 0.3, "finbert": 1.2}

_NUM_RE = re.compile(r"[0-9０-９][0-9０-９,\.]*\s*(?:%|％|億|百万|兆|円|ポイント|pt|bps|million|billion|percent)?")
_JP_RE = re.compile(r"[぀-ヿ一-鿿]")


def detect_language(text: str) -> str:
    jp = len(_JP_RE.findall(text))
    return "ja" if jp > max(20, 0.05 * len(text)) else "en"


def _count_terms(text: str, terms: list[str]) -> int:
    return sum(text.count(t) for t in terms)


def _count_terms_en(text: str, terms: list[str]) -> int:
    low = text.lower()
    n = 0
    for t in terms:
        if " " in t:
            n += low.count(t)
        else:
            n += len(re.findall(rf"\b{re.escape(t)}\w*", low))
    return n


@dataclass
class TextFeatures:
    language: str
    n_chars: int
    pos: int
    neg: int
    certain: int
    uncertain: int
    forward: int
    numeric: int
    finbert: float | None = None

    @property
    def tone(self) -> float:
        return (self.pos - self.neg) / (self.pos + self.neg + 1.0)

    @property
    def certainty(self) -> float:
        return (self.certain - self.uncertain) / (self.certain + self.uncertain + 1.0)

    @property
    def scale(self) -> float:
        """1000 文字（英語は 1000 語相当 ≒ 6000 文字）あたりの密度スケール。"""
        return max(self.n_chars, 1) / (1000.0 if self.language == "ja" else 6000.0)

    @property
    def forward_density(self) -> float:
        return min(self.forward / self.scale / 10.0, 1.0)

    @property
    def numeric_density(self) -> float:
        return min(self.numeric / self.scale / 20.0, 1.0)

    def vector(self) -> np.ndarray:
        fb = 0.0 if self.finbert is None else float(self.finbert)
        return np.array([self.tone, self.certainty, self.forward_density, self.numeric_density, fb], dtype=float)

    def as_dict(self) -> dict:
        return {
            "language": self.language, "n_chars": self.n_chars,
            "pos": self.pos, "neg": self.neg, "certain": self.certain, "uncertain": self.uncertain,
            "forward": self.forward, "numeric": self.numeric,
            "tone": round(self.tone, 4), "certainty": round(self.certainty, 4),
            "forward_density": round(self.forward_density, 4), "numeric_density": round(self.numeric_density, 4),
            "finbert": None if self.finbert is None else round(float(self.finbert), 4),
        }


def lexicon_features(text: str) -> TextFeatures:
    text = text.strip()
    lang = detect_language(text)
    if lang == "ja":
        f = TextFeatures(
            language="ja", n_chars=len(text),
            pos=_count_terms(text, JP_POSITIVE), neg=_count_terms(text, JP_NEGATIVE),
            certain=_count_terms(text, JP_CERTAIN), uncertain=_count_terms(text, JP_UNCERTAIN),
            forward=_count_terms(text, JP_FORWARD), numeric=len(_NUM_RE.findall(text)),
        )
    else:
        f = TextFeatures(
            language="en", n_chars=len(text),
            pos=_count_terms_en(text, EN_POSITIVE), neg=_count_terms_en(text, EN_NEGATIVE),
            certain=_count_terms_en(text, EN_CERTAIN), uncertain=_count_terms_en(text, EN_UNCERTAIN),
            forward=_count_terms_en(text, EN_FORWARD), numeric=len(_NUM_RE.findall(text)),
        )
    return f


# ---- FinBERT（任意） ----
_FINBERT = None
_FINBERT_TRIED = False


def _load_finbert():
    global _FINBERT, _FINBERT_TRIED
    if _FINBERT_TRIED:
        return _FINBERT
    _FINBERT_TRIED = True
    try:
        from transformers import pipeline  # type: ignore
        _FINBERT = pipeline("text-classification", model=config.FINBERT_MODEL, top_k=None, truncation=True)
        log.info("FinBERT を読み込みました: %s", config.FINBERT_MODEL)
    except Exception as e:
        log.info("FinBERT は利用不可（辞書法で継続）: %s", e)
        _FINBERT = None
    return _FINBERT


def finbert_score(text: str) -> float | None:
    """P(positive) − P(negative) をチャンク平均で返す。利用不可なら None。"""
    clf = _load_finbert()
    if clf is None:
        return None
    sents = [s.strip() for s in re.split(r"(?<=[\.\!\?])\s+", text) if len(s.strip()) > 20]
    if not sents:
        return None
    chunks, cur = [], ""
    for s in sents:
        if len(cur) + len(s) > 1500:
            chunks.append(cur); cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        chunks.append(cur)
    vals = []
    for ch in chunks[:40]:
        try:
            res = clf(ch)[0]
            probs = {r["label"].lower(): r["score"] for r in res}
            vals.append(probs.get("positive", 0.0) - probs.get("negative", 0.0))
        except Exception:
            continue
    return float(np.mean(vals)) if vals else None


# ---- 推定器 ----
@dataclass
class TextSurpriseModel:
    """ロジスティック回帰（Newton–Raphson, L2）。未学習時は事前重み。"""
    coef: np.ndarray | None = None
    intercept: float = 0.0
    n_train: int = 0
    l2: float = 1.0
    feature_names: list[str] = field(default_factory=lambda: ["tone", "certainty", "forward", "numeric", "finbert"])

    def fit(self, X: np.ndarray, y: np.ndarray, iters: int = 50) -> "TextSurpriseModel":
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        n, k = X.shape
        Xb = np.hstack([np.ones((n, 1)), X])
        w = np.zeros(k + 1)
        reg = np.eye(k + 1) * self.l2
        reg[0, 0] = 0.0
        for _ in range(iters):
            z = Xb @ w
            p = 1.0 / (1.0 + np.exp(-z))
            g = Xb.T @ (p - y) + reg @ w
            W = p * (1 - p)
            H = (Xb * W[:, None]).T @ Xb + reg
            try:
                step = np.linalg.solve(H, g)
            except np.linalg.LinAlgError:
                break
            w -= step
            if np.max(np.abs(step)) < 1e-8:
                break
        self.intercept = float(w[0])
        self.coef = w[1:]
        self.n_train = n
        return self

    def predict_prob(self, x: np.ndarray) -> float:
        x = np.asarray(x, dtype=float)
        if self.coef is None:
            w = np.array([PRIOR_WEIGHTS[n] for n in self.feature_names])
            return 1.0 / (1.0 + math.exp(-float(w @ x)))
        return 1.0 / (1.0 + math.exp(-(self.intercept + float(self.coef @ x))))

    def sue_txt(self, x: np.ndarray) -> float:
        return 2.0 * self.predict_prob(x) - 1.0

    @property
    def fitted(self) -> bool:
        return self.coef is not None

    def to_dict(self) -> dict:
        return {"coef": None if self.coef is None else self.coef.tolist(), "intercept": self.intercept,
                "n_train": self.n_train, "feature_names": self.feature_names}

    @classmethod
    def from_dict(cls, d: dict) -> "TextSurpriseModel":
        m = cls()
        if d.get("coef") is not None:
            m.coef = np.array(d["coef"], dtype=float)
            m.intercept = float(d.get("intercept", 0.0))
            m.n_train = int(d.get("n_train", 0))
        return m


def score_text(text: str, model: TextSurpriseModel | None = None, backend: str = config.TEXT_MODEL_BACKEND) -> dict:
    """1 テキストを採点。返り値は UI 向けの辞書。"""
    model = model or TextSurpriseModel()
    f = lexicon_features(text)
    used = "lexicon"
    if backend in ("auto", "finbert") and f.language == "en":
        fb = finbert_score(text)
        if fb is not None:
            f.finbert = fb
            used = "finbert+lexicon"
    x = f.vector()
    s = float(model.sue_txt(x))
    return {
        "sue_txt": round(s, 4),
        "confidence_score": round(50.0 + 50.0 * s, 1),   # UI 用 0..100
        "backend": used,
        "model": "logit" if model.fitted else "prior",
        "features": f.as_dict(),
    }


def plain_summary(features: dict) -> str:
    """初心者向けの一文説明。"""
    tone = features.get("tone", 0.0)
    cert = features.get("certainty", 0.0)
    t = "前向き" if tone > 0.15 else ("慎重" if tone < -0.15 else "中立")
    c = "言い切り型" if cert > 0.15 else ("含みを残す" if cert < -0.15 else "ふつう")
    return f"言葉づかいは{t}で、見通しの語り方は{c}です。"
