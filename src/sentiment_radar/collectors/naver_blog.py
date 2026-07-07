"""블로그 수집기 — Naver 검색 API (블로그). 리테일 심리 대리 지표.

docs: https://developers.naver.com/docs/serviceapi/search/blog/blog.md
필요 환경변수: NAVER_CLIENT_ID, NAVER_CLIENT_SECRET
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from ..config import Theme, env
from ..models import Item
from ..utils.text import strip_html
from .base import BaseCollector

log = logging.getLogger(__name__)

NAVER_BLOG_URL = "https://openapi.naver.com/v1/search/blog.json"


def _parse_postdate(raw: str | None) -> str | None:
    """Naver 블로그 postdate(YYYYMMDD) → ISO8601 UTC(자정)."""
    if not raw:
        return None
    try:
        dt = datetime.strptime(raw, "%Y%m%d").replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        return None


class NaverBlogCollector(BaseCollector):
    source_type = "blog"

    def __init__(self) -> None:
        super().__init__()
        self.client_id = env("NAVER_CLIENT_ID")
        self.client_secret = env("NAVER_CLIENT_SECRET")

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def collect(self, theme: Theme) -> list[Item]:
        if not self.enabled:
            log.warning("[naver_blog] NAVER_CLIENT_ID/SECRET 미설정 — 스킵")
            return []

        headers = {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret,
            "User-Agent": self.user_agent,
        }
        items: list[Item] = []
        seen: set[str] = set()

        for kw in theme.keywords_ko:
            for entry in self._search(kw, headers):
                title = strip_html(entry.get("title"))
                desc = strip_html(entry.get("description"))
                url = entry.get("link") or ""
                if not self.is_relevant(theme, title, desc) or url in seen:
                    continue
                seen.add(url)
                items.append(self.finalize(Item(
                    theme=theme.theme, source_type=self.source_type,
                    source_name=entry.get("bloggername") or "naver_blog",
                    title=title, content_snippet=desc, url=url,
                    author=entry.get("bloggername") or "",
                    published_at=_parse_postdate(entry.get("postdate")),
                    lang="ko", keyword_matched=kw,
                )))
                if len(items) >= self.per_source_limit:
                    return items
        return items

    def _search(self, keyword: str, headers: dict[str, str]) -> list[dict]:
        self.throttle()
        params = {"query": keyword, "display": 100, "sort": "date"}
        try:
            resp = requests.get(NAVER_BLOG_URL, headers=headers, params=params, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            log.error("[naver_blog] '%s' 요청 실패: %s", keyword, e)
            return []
        return resp.json().get("items", [])
