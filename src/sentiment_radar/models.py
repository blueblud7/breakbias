"""표준 데이터 모델 — Collector 는 Item 을 반환한다."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
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


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
