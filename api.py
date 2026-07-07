#!/usr/bin/env python3
"""수집 스케줄러 API (M5) — FastAPI.

수동 트리거 + 상태/비용/헬스 조회. 크론 자동실행은 scheduler.py 가 담당.

실행: uvicorn api:app --reload
엔드포인트:
    GET  /health            서비스 헬스
    GET  /status/{theme}    데이터 무결성 리포트
    GET  /cost              LLM 비용 요약
    POST /run/{theme}       파이프라인 수동 실행
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from fastapi import FastAPI, HTTPException

from sentiment_radar.config import list_themes
from sentiment_radar.db import get_db
from sentiment_radar.health import cost_summary, health_report
from sentiment_radar.orchestrate import run_full_pipeline

app = FastAPI(title="Market Sentiment Radar API", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok", "themes": list_themes()}


@app.get("/status/{theme}")
def status(theme: str):
    with get_db() as db:
        rep = health_report(db, theme)
        return {"theme": theme, "ok": rep.ok,
                "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail}
                           for c in rep.checks]}


@app.get("/cost")
def cost(days: int = 30):
    with get_db() as db:
        return cost_summary(db, days=days)


@app.post("/run/{theme}")
def run(theme: str, collect: bool = True, commentary: bool = True):
    if theme not in (list_themes() or []):
        raise HTTPException(status_code=404, detail=f"알 수 없는 테마: {theme}")
    with get_db() as db:
        rep = run_full_pipeline(theme, db=db, do_collect=collect,
                                do_commentary=commentary)
        return rep.as_dict()
