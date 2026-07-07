"""BaseCollector — 모든 수집기의 공통 인터페이스.

각 수집기는 collect() 를 구현하고 Item 리스트를 반환한다.
공통 처리(요청 간격, content 길이 제한, 관련성 규칙 필터, 해시/URL 정규화)는
여기서 담당한다.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

from ..config import Theme, settings
from ..models import Item
from ..utils.text import content_hash, normalize_url, truncate

log = logging.getLogger(__name__)


class BaseCollector(ABC):
    """수집기 베이스.

    하위 클래스는 `source_type` 속성과 `collect(theme)` 를 구현한다.
    `collect` 내부에서는 원시 데이터를 Item 으로 만들어 `self.finalize` 를
    거친 뒤 반환하는 것을 권장한다.
    """

    #: news_kr / news_global / report / blog / youtube / reddit / telegram
    source_type: str = ""

    def __init__(self) -> None:
        if not self.source_type:
            raise NotImplementedError("source_type 을 정의해야 합니다.")
        cfg = settings().get("collection", {})
        self.request_interval = float(cfg.get("request_interval_sec", 2.0))
        self.max_chars = int(cfg.get("max_content_chars", 3000))
        self.per_source_limit = int(cfg.get("per_source_limit", 100))
        self.user_agent = cfg.get("user_agent", "market-sentiment-radar/0.1")
        self._last_request_ts = 0.0

    # --- 공개 API ---
    @abstractmethod
    def collect(self, theme: Theme) -> list[Item]:
        """테마에 대해 아이템을 수집해 반환."""

    # --- 공통 헬퍼 ---
    def throttle(self) -> None:
        """요청 간 최소 간격 보장 (스크레이핑 예의)."""
        elapsed = time.monotonic() - self._last_request_ts
        wait = self.request_interval - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.monotonic()

    def is_relevant(self, theme: Theme, *texts: str) -> bool:
        """관련성 규칙 필터: must_include_any 중 하나라도 포함하면 통과.

        목록이 비어 있으면 항상 통과. (애매한 케이스는 이후 LLM 이 판정)
        """
        needles = [w.lower() for w in theme.relevance_must_include_any]
        if not needles:
            return True
        haystack = " ".join(t.lower() for t in texts if t)
        return any(n in haystack for n in needles)

    def finalize(self, item: Item) -> Item:
        """공통 후처리: 길이 제한, URL 정규화, 해시 부여."""
        item.content_snippet = truncate(item.content_snippet, self.max_chars)
        item.url_normalized = normalize_url(item.url)
        item.content_hash = content_hash(item.title, item.url)
        return item
