"""커뮤니티 수집기 — Reddit API (praw).

r/stocks, r/wallstreetbets, r/Semiconductors 등에서 키워드 검색.
필요 환경변수: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..config import Theme, env, settings
from ..models import Item
from ..utils.text import truncate
from .base import BaseCollector

log = logging.getLogger(__name__)


class RedditCollector(BaseCollector):
    source_type = "reddit"

    def __init__(self, fetch_fn=None) -> None:
        super().__init__()
        self.client_id = env("REDDIT_CLIENT_ID")
        self.client_secret = env("REDDIT_CLIENT_SECRET")
        self.ua = env("REDDIT_USER_AGENT", "market-sentiment-radar/0.1")
        cfg = settings().get("sources", {}).get("reddit", {})
        self.subreddits = cfg.get("subreddits", ["stocks", "Semiconductors"])
        self.limit = int(cfg.get("limit_per_subreddit", 50))
        self._fetch_fn = fetch_fn            # 테스트 주입용
        self._reddit = None

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret) or self._fetch_fn is not None

    def collect(self, theme: Theme) -> list[Item]:
        if not self.enabled:
            log.warning("[reddit] REDDIT 자격증명 미설정 — 스킵")
            return []
        items: list[Item] = []
        seen: set[str] = set()

        # 영어권 커뮤니티이므로 영문 키워드 우선
        keywords = theme.keywords_en or theme.keywords_ko
        for sub in self.subreddits:
            for kw in keywords:
                for post in self._fetch_posts(sub, kw):
                    pid = post.get("id")
                    if not pid or pid in seen:
                        continue
                    title = post.get("title", "")
                    body = post.get("selftext", "")
                    if not self.is_relevant(theme, title, body):
                        continue
                    seen.add(pid)
                    items.append(self.finalize(Item(
                        theme=theme.theme, source_type=self.source_type,
                        source_name=f"r/{sub}",
                        title=title, content_snippet=truncate(body, self.max_chars),
                        url=post.get("url", ""),
                        author=post.get("author", ""),
                        published_at=_ts_to_iso(post.get("created_utc")),
                        reach_score=float(post.get("score", 0) or 0),
                        lang="en", keyword_matched=kw,
                    )))
                    if len(items) >= self.per_source_limit:
                        return items
        return items

    def _fetch_posts(self, subreddit: str, keyword: str) -> list[dict]:
        if self._fetch_fn is not None:
            return self._fetch_fn(subreddit, keyword)
        self.throttle()
        try:
            reddit = self._get_client()
            sub = reddit.subreddit(subreddit)
            out = []
            for s in sub.search(keyword, sort="relevance", limit=self.limit):
                out.append({
                    "id": s.id, "title": s.title, "selftext": s.selftext or "",
                    "url": f"https://reddit.com{s.permalink}",
                    "author": str(s.author) if s.author else "",
                    "created_utc": s.created_utc, "score": s.score,
                })
            return out
        except Exception as e:  # API 오류 방어
            log.error("[reddit] r/%s '%s' 실패: %s", subreddit, keyword, e)
            return []

    def _get_client(self):
        if self._reddit is None:
            import praw
            self._reddit = praw.Reddit(
                client_id=self.client_id, client_secret=self.client_secret,
                user_agent=self.ua,
            )
            self._reddit.read_only = True
        return self._reddit


def _ts_to_iso(ts) -> str | None:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (ValueError, OSError):
        return None
