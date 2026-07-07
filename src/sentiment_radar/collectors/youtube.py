"""유튜브 수집기 — YouTube Data API v3.

키워드 검색 → 상위 N개 영상의 제목/설명/조회수 수집.
채널 화이트리스트(config)로 필터, 자막(youtube-transcript-api) 앞 3000자 보조 사용.
필요 환경변수: YOUTUBE_API_KEY
"""

from __future__ import annotations

import logging
from datetime import timezone

from dateutil import parser as dateparser

from ..config import Theme, env, settings
from ..models import Item
from .base import BaseCollector

log = logging.getLogger(__name__)


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


class YouTubeCollector(BaseCollector):
    source_type = "youtube"

    def __init__(self, fetch_fn=None, transcript_fn=None) -> None:
        super().__init__()
        self.api_key = env("YOUTUBE_API_KEY")
        cfg = settings().get("sources", {}).get("youtube", {})
        self.max_results = int(cfg.get("max_results_per_keyword", 15))
        self.use_transcript = bool(cfg.get("use_transcript", True))
        self.allowed_channels = set(cfg.get("channels_kr", []) or []) | set(
            cfg.get("channels_global", []) or []
        )
        self._fetch_fn = fetch_fn            # 테스트 주입용
        self._transcript_fn = transcript_fn
        self._client = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key) or self._fetch_fn is not None

    def collect(self, theme: Theme) -> list[Item]:
        if not self.enabled:
            log.warning("[youtube] YOUTUBE_API_KEY 미설정 — 스킵")
            return []
        items: list[Item] = []
        seen: set[str] = set()

        for kw in theme.all_keywords:
            for v in self._fetch_videos(kw):
                vid = v.get("videoId")
                if not vid or vid in seen:
                    continue
                # 화이트리스트 필터 (비어 있으면 전체 허용)
                if self.allowed_channels and v.get("channelId") not in self.allowed_channels:
                    continue
                title = v.get("title", "")
                desc = v.get("description", "")
                if not self.is_relevant(theme, title, desc):
                    continue
                seen.add(vid)

                snippet = desc
                if self.use_transcript:
                    tr = self._transcript(vid)
                    if tr:
                        snippet = (desc + "\n" + tr)[: self.max_chars]

                lang = "ko" if kw in theme.keywords_ko else "en"
                items.append(self.finalize(Item(
                    theme=theme.theme, source_type=self.source_type,
                    source_name=v.get("channelTitle") or "youtube",
                    title=title, content_snippet=snippet,
                    url=f"https://www.youtube.com/watch?v={vid}",
                    author=v.get("channelTitle") or "",
                    published_at=_parse_iso(v.get("publishedAt")),
                    reach_score=float(v.get("viewCount", 0) or 0),
                    lang=lang, keyword_matched=kw,
                )))
                if len(items) >= self.per_source_limit:
                    return items
        return items

    # --- 네트워크 경계 (테스트 시 주입/오버라이드) ---
    def _fetch_videos(self, keyword: str) -> list[dict]:
        if self._fetch_fn is not None:
            return self._fetch_fn(keyword)
        self.throttle()
        try:
            client = self._get_client()
            search = client.search().list(
                q=keyword, part="id", type="video", order="relevance",
                maxResults=self.max_results,
            ).execute()
            ids = [it["id"]["videoId"] for it in search.get("items", [])
                   if it.get("id", {}).get("videoId")]
            if not ids:
                return []
            details = client.videos().list(
                part="snippet,statistics", id=",".join(ids)
            ).execute()
        except Exception as e:  # API 오류 방어
            log.error("[youtube] '%s' 요청 실패: %s", keyword, e)
            return []

        out = []
        for it in details.get("items", []):
            sn = it.get("snippet", {})
            st = it.get("statistics", {})
            out.append({
                "videoId": it.get("id"),
                "title": sn.get("title", ""),
                "description": sn.get("description", ""),
                "channelId": sn.get("channelId", ""),
                "channelTitle": sn.get("channelTitle", ""),
                "publishedAt": sn.get("publishedAt"),
                "viewCount": st.get("viewCount", 0),
            })
        return out

    def _transcript(self, video_id: str) -> str:
        if self._transcript_fn is not None:
            return self._transcript_fn(video_id)
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            parts = YouTubeTranscriptApi.get_transcript(video_id, languages=["ko", "en"])
            return " ".join(p["text"] for p in parts)[: self.max_chars]
        except Exception:  # 자막 없음/비공개 등은 조용히 무시
            return ""

    def _get_client(self):
        if self._client is None:
            from googleapiclient.discovery import build
            self._client = build("youtube", "v3", developerKey=self.api_key,
                                 cache_discovery=False)
        return self._client
