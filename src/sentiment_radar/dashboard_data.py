"""대시보드 데이터 준비 (순수 함수, pandas 비의존).

Streamlit app.py 는 이 함수들의 결과를 받아 시각화만 담당한다.
여기 로직(정렬·델타·피벗)은 단위 테스트로 검증한다.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .models import KST


def _kst_today() -> str:
    return datetime.now(KST).date().isoformat()


def _row_to_dict(r) -> dict[str, Any]:
    return {k: r[k] for k in r.keys()}


def get_timeseries(db, theme: str, scope: str = "all", limit: int = 60) -> list[dict]:
    """날짜 오름차순 집계 시계열."""
    rows = db.fetch_aggregates(theme, scope, limit=limit)
    return [_row_to_dict(r) for r in reversed(rows)]


def get_today_gauge(db, theme: str, date: str | None = None) -> dict[str, Any] | None:
    """오늘 게이지용 값 + 전일 대비 델타."""
    from datetime import timedelta

    date = date or _kst_today()
    today = db.fetch_aggregate_one(theme, date, "all")
    if today is None:
        # 최신 가용 날짜로 폴백
        rows = db.fetch_aggregates(theme, "all", limit=1)
        if not rows:
            return None
        today = rows[0]
        date = today["bucket_date"]

    d = datetime.strptime(date, "%Y-%m-%d").date()
    yday = db.fetch_aggregate_one(theme, (d - timedelta(days=1)).isoformat(), "all")
    delta = None
    if yday is not None and yday["nsi_wt"] is not None:
        delta = round(today["nsi_wt"] - yday["nsi_wt"], 1)

    return {
        "date": date,
        "nsi_wt": round(today["nsi_wt"], 1),
        "nsi_raw": round(today["nsi_raw"], 1),
        "delta_wt": delta,
        "pct_raw": [round(today["pct_pos_raw"], 1), round(today["pct_neu_raw"], 1),
                    round(today["pct_neg_raw"], 1)],
        "pct_wt": [round(today["pct_pos_wt"], 1), round(today["pct_neu_wt"], 1),
                   round(today["pct_neg_wt"], 1)],
        "extreme": bool(today["extreme_flag"]),
        "divergence": (round(today["divergence"], 1)
                       if today["divergence"] is not None else None),
        "n_items": today["n_items"],
    }


def get_source_matrix(db, theme: str, limit: int = 30) -> dict[str, Any]:
    """소스별 x 날짜 NSI 히트맵 데이터."""
    sources = [s for s in db.distinct_source_types(theme)
               if s not in ("all", "institutional", "retail")]
    dates: set[str] = set()
    nsi: dict[str, dict[str, float]] = {}
    for s in sources:
        nsi[s] = {}
        for r in db.fetch_aggregates(theme, s, limit=limit):
            dates.add(r["bucket_date"])
            nsi[s][r["bucket_date"]] = round(r["nsi_wt"], 1)
    return {"sources": sorted(sources), "dates": sorted(dates), "nsi": nsi}


# 반대 정렬 우선순위: my_view 의 반대 센티먼트를 맨 앞으로
def _opposite_first_key(my_view: str):
    order = {"positive": 0, "neutral": 1, "negative": 2}  # 기본
    if my_view == "positive":      # 내가 긍정 → 부정부터
        order = {"negative": 0, "neutral": 1, "positive": 2}
    elif my_view == "negative":    # 내가 부정 → 긍정부터
        order = {"positive": 0, "neutral": 1, "negative": 2}

    def key(item: dict) -> tuple:
        return (order.get(item["sentiment"], 3), -(item.get("reach_score") or 0))
    return key


def get_items(db, theme: str, *, my_view: str = "none", sentiment: str | None = None,
              source_type: str | None = None, limit: int = 200) -> list[dict]:
    """아이템 테이블. my_view 반대 센티먼트를 우선 정렬 (확증편향 방지)."""
    rows = db.fetch_classified_detailed(theme)
    items = []
    for r in rows:
        if sentiment and r["sentiment"] != sentiment:
            continue
        if source_type and r["source_type"] != source_type:
            continue
        items.append({
            "id": r["id"], "source_type": r["source_type"],
            "source_name": r["source_name"], "title": r["title"], "url": r["url"],
            "sentiment": r["sentiment"], "confidence": r["confidence"],
            "one_line_summary": r["one_line_summary"], "key_argument": r["key_argument"],
            "reach_score": r["reach_score"], "published_at": r["published_at"],
        })
    if my_view in ("positive", "negative"):
        items.sort(key=_opposite_first_key(my_view))
    return items[:limit]


def get_commentary(db, theme: str, date: str | None = None) -> dict[str, Any] | None:
    row = db.fetch_commentary(theme, date)
    if row is None:
        return None
    try:
        cas = json.loads(row["counter_args"] or "[]")
    except json.JSONDecodeError:
        cas = []
    return {"date": row["bucket_date"], "commentary": row["commentary"],
            "counter_arguments": cas, "model": row["model"]}


def get_prices(db, symbol: str, limit: int = 90) -> list[dict]:
    return [_row_to_dict(r) for r in db.fetch_price_series(symbol, limit=limit)]


def get_attention_ratio(db, theme: str, limit: int = 90) -> list[dict]:
    rows = db.fetch_attention(theme, metric="trends_ratio", limit=limit)
    return [{"bucket_date": r["bucket_date"], "keyword": r["keyword"],
             "value": r["value"]} for r in reversed(rows)]
