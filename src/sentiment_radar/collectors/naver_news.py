"""국내 뉴스 수집기 — Naver 검색 API (뉴스).

docs: https://developers.naver.com/docs/serviceapi/search/news/news.md
필요 환경변수: NAVER_CLIENT_ID, NAVER_CLIENT_SECRET
"""

from __future__ import annotations

import logging
from datetime import timezone
from email.utils import parsedate_to_datetime

import requests

from ..config import Theme, env
from ..models import Item
from ..utils.text import strip_html
from .base import BaseCollector

log = logging.getLogger(__name__)

NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"


def _parse_pubdate(raw: str | None) -> str | None:
    """Naver 의 RFC822 형식(pubDate)을 ISO8601 UTC 로 변환."""
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


class NaverNewsCollector(BaseCollector):
    source_type = "news_kr"

    def __init__(self) -> None:
        super().__init__()
        self.client_id = env("NAVER_CLIENT_ID")
        self.client_secret = env("NAVER_CLIENT_SECRET")

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def collect(self, theme: Theme) -> list[Item]:
        if not self.enabled:
            log.warning("[naver_news] NAVER_CLIENT_ID/SECRET 미설정 — 스킵")
            return []

        headers = {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret,
            "User-Agent": self.user_agent,
        }
        items: list[Item] = []
        seen_local: set[str] = set()

        for kw in theme.keywords_ko:
            fetched = self._search(kw, headers)
            for entry in fetched:
                title = strip_html(entry.get("title"))
                desc = strip_html(entry.get("description"))
                url = entry.get("originallink") or entry.get("link") or ""

                if not self.is_relevant(theme, title, desc):
                    continue
                if url in seen_local:
                    continue
                seen_local.add(url)

                item = Item(
                    theme=theme.theme,
                    source_type=self.source_type,
                    source_name="naver_news",
                    title=title,
                    content_snippet=desc,
                    url=url,
                    published_at=_parse_pubdate(entry.get("pubDate")),
                    lang="ko",
                    keyword_matched=kw,
                )
                items.append(self.finalize(item))
                if len(items) >= self.per_source_limit:
                    return items
        return items

    def _search(self, keyword: str, headers: dict[str, str]) -> list[dict]:
        """단일 키워드 검색 (최신순, 최대 100건)."""
        self.throttle()
        params = {"query": keyword, "display": 100, "sort": "date"}
        try:
            resp = requests.get(
                NAVER_NEWS_URL, headers=headers, params=params, timeout=15
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            log.error("[naver_news] '%s' 요청 실패: %s", keyword, e)
            return []
        return resp.json().get("items", [])
