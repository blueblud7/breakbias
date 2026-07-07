"""수집기 레지스트리."""

from __future__ import annotations

from .base import BaseCollector  # noqa: F401
from .naver_news import NaverNewsCollector
from .naver_blog import NaverBlogCollector
from .newsapi import NewsAPICollector
from .youtube import YouTubeCollector
from .reddit import RedditCollector
from .report_naver import NaverReportCollector

# source_type 문자열 -> Collector 클래스
REGISTRY: dict[str, type[BaseCollector]] = {
    "news_kr": NaverNewsCollector,
    "news_global": NewsAPICollector,
    "blog": NaverBlogCollector,
    "youtube": YouTubeCollector,
    "reddit": RedditCollector,
    "report": NaverReportCollector,
}


def available() -> list[str]:
    return sorted(REGISTRY.keys())
