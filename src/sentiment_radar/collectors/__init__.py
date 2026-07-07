"""수집기 레지스트리."""

from __future__ import annotations

from .base import BaseCollector  # noqa: F401
from .naver_news import NaverNewsCollector
from .newsapi import NewsAPICollector

# source_type 문자열 -> Collector 클래스
REGISTRY: dict[str, type[BaseCollector]] = {
    "news_kr": NaverNewsCollector,
    "news_global": NewsAPICollector,
}


def available() -> list[str]:
    return sorted(REGISTRY.keys())
