"""해외 뉴스 수집기 — NewsAPI (https://newsapi.org).

필요 환경변수: NEWSAPI_KEY
무료 플랜은 최근 약 30일, 요청 수 제한이 있으니 백필 시 주의.
(대안으로 GDELT 를 쓸 경우 동일 인터페이스로 별도 collector 추가)
"""

from __future__ import annotations

import logging
from datetime import timezone

from dateutil import parser as dateparser
import requests

from ..config import Theme, env
from ..models import Item
from ..utils.text import strip_html
from .base import BaseCollector

log = logging.getLogger(__name__)

NEWSAPI_URL = "https://newsapi.org/v2/everything"


def _parse_iso(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        dt = dateparser.parse(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        return None


class NewsAPICollector(BaseCollector):
    source_type = "news_global"

    def __init__(self) -> None:
        super().__init__()
        self.api_key = env("NEWSAPI_KEY")

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def collect(self, theme: Theme) -> list[Item]:
        if not self.enabled:
            log.warning("[newsapi] NEWSAPI_KEY 미설정 — 스킵")
            return []

        headers = {"User-Agent": self.user_agent, "X-Api-Key": self.api_key}
        items: list[Item] = []
        seen_local: set[str] = set()

        for kw in theme.keywords_en:
            for entry in self._search(kw, headers):
                title = strip_html(entry.get("title"))
                desc = strip_html(entry.get("description") or entry.get("content"))
                url = entry.get("url") or ""

                if not title or not self.is_relevant(theme, title, desc):
                    continue
                if url in seen_local:
                    continue
                seen_local.add(url)

                source_name = (entry.get("source") or {}).get("name") or "newsapi"
                item = Item(
                    theme=theme.theme,
                    source_type=self.source_type,
                    source_name=source_name,
                    title=title,
                    content_snippet=desc,
                    url=url,
                    author=entry.get("author") or "",
                    published_at=_parse_iso(entry.get("publishedAt")),
                    lang="en",
                    keyword_matched=kw,
                )
                items.append(self.finalize(item))
                if len(items) >= self.per_source_limit:
                    return items
        return items

    def _search(self, keyword: str, headers: dict[str, str]) -> list[dict]:
        self.throttle()
        params = {
            "q": keyword,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": 100,
        }
        try:
            resp = requests.get(
                NEWSAPI_URL, headers=headers, params=params, timeout=15
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            log.error("[newsapi] '%s' 요청 실패: %s", keyword, e)
            return []
        payload = resp.json()
        if payload.get("status") != "ok":
            log.error("[newsapi] '%s' 응답 오류: %s", keyword, payload.get("message"))
            return []
        return payload.get("articles", [])
