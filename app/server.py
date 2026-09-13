"""FastAPI サーバ: UI の静的配信、結果 API、更新ジョブ、テキスト登録。

  GET  /                      UI
  GET  /results.json          最新結果（no-cache）
  GET  /api/status            更新ジョブの進捗
  POST /api/refresh           バックグラウンドでパイプライン再実行
  POST /api/transcripts       {symbol, date, text} を保存し、再計算をキック
  GET  /api/transcripts/{sym} 登録済みテキスト一覧
  POST /api/score-text        {text} をその場で採点（保存しない・お試し用）
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, pipeline
from .data import transcripts
from .models import text as textmod

log = logging.getLogger("ripple.server")
app = FastAPI(title="RippleDrift", docs_url=None, redoc_url=None)

_state = {"running": False, "stage": "", "pct": 0.0, "message": "", "started": None, "finished": None, "error": None}
_lock = threading.Lock()


def _progress(stage: str, pct: float, message: str):
    _state.update({"stage": stage, "pct": round(pct, 1), "message": message})


def _job(force: bool):
    try:
        pipeline.run(progress_cb=_progress, force=force)
        _state["error"] = None
    except Exception as e:  # UI に表示するため握りつぶさず記録
        log.exception("パイプライン失敗")
        _state["error"] = str(e)
    finally:
        _state["running"] = False
        _state["finished"] = time.time()


def start_refresh(force: bool = False) -> bool:
    with _lock:
        if _state["running"]:
            return False
        _state.update({"running": True, "stage": "start", "pct": 0.0, "message": "開始", "started": time.time(),
                       "finished": None, "error": None})
        threading.Thread(target=_job, args=(force,), daemon=True).start()
        return True


@app.get("/")
def index():
    return FileResponse(config.WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/results.json")
def results():
    if not config.RESULTS_PATH.exists():
        return JSONResponse({"empty": True}, headers={"Cache-Control": "no-cache"})
    return FileResponse(config.RESULTS_PATH, media_type="application/json", headers={"Cache-Control": "no-cache"})


@app.get("/api/status")
def status():
    return {**_state, "has_results": config.RESULTS_PATH.exists()}


class RefreshBody(BaseModel):
    force: bool = False


@app.post("/api/refresh")
def refresh(body: RefreshBody | None = None):
    started = start_refresh(force=bool(body and body.force))
    return {"started": started, **_state}


class TranscriptBody(BaseModel):
    symbol: str
    date: str
    text: str
    kind: str = "manual"


@app.post("/api/transcripts")
def add_transcript(body: TranscriptBody):
    if len(body.text.strip()) < 40:
        raise HTTPException(400, "テキストが短すぎます（40文字以上）")
    try:
        path = transcripts.save_transcript(body.symbol, body.date, body.text, body.kind)
    except ValueError as e:
        raise HTTPException(400, str(e))
    model = textmod.TextSurpriseModel()
    if pipeline.MODEL_PATH.exists():
        try:
            model = textmod.TextSurpriseModel.from_dict(json.loads(pipeline.MODEL_PATH.read_text()))
        except Exception:
            pass
    score = textmod.score_text(body.text, model)
    started = start_refresh(force=False)
    return {"saved": str(path), "score": score, "refresh_started": started}


@app.get("/api/transcripts/{symbol}")
def list_transcripts(symbol: str):
    allt = transcripts.load_transcripts()
    sym = symbol if "." in symbol else f"{symbol}.T"
    items = allt.get(sym, [])
    return [{"date": i["date"], "kind": i["kind"], "chars": len(i["text"])} for i in items]


class ScoreBody(BaseModel):
    text: str


@app.post("/api/score-text")
def score_text(body: ScoreBody):
    return textmod.score_text(body.text, backend="lexicon")


app.mount("/static", StaticFiles(directory=str(config.WEB_DIR)), name="static")


def main():
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    uvicorn.run("app.server:app", host=config.HOST, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
