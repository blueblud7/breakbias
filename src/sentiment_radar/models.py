"""표준 데이터 모델 — Collector 는 Item 을 반환한다."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

# 허용 source_type 값
SOURCE_TYPES = {
    "news_kr",
    "news_global",
    "report",
    "blog",
    "youtube",
    "reddit",
    "telegram",
}

SENTIMENTS = {"positive", "neutral", "negative"}
TIME_HORIZONS = {"short", "mid", "long", "unclear"}

KST = timezone(timedelta(hours=9))


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_kst_date(iso_ts: str | None) -> str | None:
    """ISO8601 타임스탬프를 KST 기준 날짜(YYYY-MM-DD) 로 변환."""
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(iso_ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).date().isoformat()


@dataclass
class Item:
    """수집된 단일 아이템 (표준 스키마)."""

    theme: str
    source_type: str
    title: str
    source_name: str = ""
    content_snippet: str = ""
    url: str = ""
    url_normalized: str = ""
    author: str = ""
    published_at: str | None = None      # ISO8601 UTC
    collected_at: str = field(default_factory=utcnow_iso)
    reach_score: float = 0.0
    lang: str = "ko"
    keyword_matched: str = ""
    content_hash: str = ""

    def __post_init__(self) -> None:
        if self.source_type not in SOURCE_TYPES:
            raise ValueError(
                f"알 수 없는 source_type: {self.source_type!r} "
                f"(허용: {sorted(SOURCE_TYPES)})"
            )
        # content_snippet 길이 제한은 collector 공통 처리에서 적용

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Classification:
    """gpt-5-nano 개별 분류 결과 (아이템 1:1)."""

    item_id: int
    sentiment: str                     # positive|neutral|negative
    confidence: float                  # 0.0~1.0
    one_line_summary: str = ""
    key_argument: str = ""
    time_horizon: str = "unclear"      # short|mid|long|unclear
    is_opinion: bool = True
    model: str = ""
    classified_at: str = field(default_factory=utcnow_iso)

    def __post_init__(self) -> None:
        if self.sentiment not in SENTIMENTS:
            raise ValueError(f"알 수 없는 sentiment: {self.sentiment!r}")
        if self.time_horizon not in TIME_HORIZONS:
            self.time_horizon = "unclear"
        # confidence 클램핑
        self.confidence = max(0.0, min(1.0, float(self.confidence)))

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["is_opinion"] = 1 if self.is_opinion else 0
        return row
