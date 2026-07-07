"""X(Twitter) 수집기 — 인터페이스만 유지 (v1 비용 문제로 비활성).

향후 활성화 시 collect() 를 구현하고 REGISTRY 에 등록한다.
"""

from __future__ import annotations

import logging

from ..config import Theme
from ..models import Item
from .base import BaseCollector

log = logging.getLogger(__name__)


class TwitterCollector(BaseCollector):
    source_type = "telegram"  # 표준 타입 재사용(커뮤니티군). 실제 소스명은 source_name 으로.

    @property
    def enabled(self) -> bool:
        return False  # v1 제외

    def collect(self, theme: Theme) -> list[Item]:
        log.info("[twitter] v1 비활성 — 인터페이스만 유지")
        return []
