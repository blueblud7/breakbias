"""M3 수집기 테스트 — 네트워크 없이 주입된 raw 응답으로 매핑/필터 로직 검증."""

import pytest

from sentiment_radar.config import Theme
from sentiment_radar.collectors.report_naver import (
    NaverReportCollector,
    parse_research_list,
)
from sentiment_radar.collectors.reddit import RedditCollector
from sentiment_radar.collectors.youtube import YouTubeCollector


@pytest.fixture
def theme():
    return Theme(
        theme="semiconductor", display_name="반도체", my_view="none",
        keywords_ko=["반도체", "삼성전자"], keywords_en=["semiconductor", "SK Hynix"],
        relevance_must_include_any=["반도체", "semiconductor", "chip", "hynix", "삼성"],
    )


# ---------- 리포트 스크레이퍼 ----------

SAMPLE_HTML = """
<table class="type_1">
  <tr><th>제목</th><th>증권사</th><th>작성일</th></tr>
  <tr>
    <td class="title"><a href="read.naver?nid=1">반도체 업황 반등 신호</a></td>
    <td>미래에셋증권</td><td>26.07.01</td>
  </tr>
  <tr>
    <td class="title"><a href="read.naver?nid=2">2차전지 단기 조정 전망</a></td>
    <td>삼성증권</td><td>26.07.02</td>
  </tr>
</table>
"""


def test_parse_research_list():
    rows = parse_research_list(SAMPLE_HTML)
    assert len(rows) == 2
    r0 = rows[0]
    assert r0["title"] == "반도체 업황 반등 신호"
    assert r0["url"].startswith("http")
    assert r0["broker"] == "미래에셋증권"
    assert r0["date"].startswith("2026-07-01")


def test_report_collector_filters_relevance(theme):
    col = NaverReportCollector(fetch_fn=lambda page: SAMPLE_HTML if page == 1 else "")
    items = col.collect(theme)
    # "반도체..." 는 통과, "2차전지..." 는 관련성 필터로 제외
    assert len(items) == 1
    assert items[0].source_type == "report"
    assert "반도체" in items[0].title


# ---------- 유튜브 ----------

def test_youtube_maps_and_filters(theme):
    videos = [
        {"videoId": "aaa", "title": "반도체 슈퍼사이클 온다", "description": "HBM 수요",
         "channelId": "C1", "channelTitle": "삼프로TV", "publishedAt": "2026-07-01T00:00:00Z",
         "viewCount": "50000"},
        {"videoId": "bbb", "title": "오늘 날씨 브이로그", "description": "산책",
         "channelId": "C9", "channelTitle": "여행채널", "publishedAt": "2026-07-01T00:00:00Z",
         "viewCount": "100"},
    ]
    col = YouTubeCollector(fetch_fn=lambda kw: videos, transcript_fn=lambda vid: "")
    col.use_transcript = False
    items = col.collect(theme)
    # 관련 영상 1개만, reach_score=조회수
    assert len(items) == 1
    assert items[0].reach_score == 50000.0
    assert items[0].url == "https://www.youtube.com/watch?v=aaa"


def test_youtube_channel_whitelist(theme):
    videos = [{"videoId": "aaa", "title": "반도체 전망", "description": "chip",
               "channelId": "C1", "channelTitle": "삼프로", "publishedAt": None,
               "viewCount": "10"}]
    col = YouTubeCollector(fetch_fn=lambda kw: videos, transcript_fn=lambda vid: "")
    col.use_transcript = False
    col.allowed_channels = {"C_OTHER"}       # 화이트리스트에 없음
    assert col.collect(theme) == []
    col.allowed_channels = {"C1"}            # 화이트리스트 허용
    assert len(col.collect(theme)) == 1


# ---------- Reddit ----------

def test_reddit_maps_posts(theme):
    posts = [
        {"id": "p1", "title": "SK Hynix HBM demand surges", "selftext": "bullish",
         "url": "https://reddit.com/p1", "author": "u1",
         "created_utc": 1_780_000_000, "score": 320},
        {"id": "p2", "title": "Cat pictures thread", "selftext": "meow",
         "url": "https://reddit.com/p2", "author": "u2",
         "created_utc": 1_780_000_000, "score": 5},
    ]
    col = RedditCollector(fetch_fn=lambda sub, kw: posts)
    col.subreddits = ["Semiconductors"]      # 단일 서브레딧
    items = col.collect(theme)
    # 관련(hynix) 1건만
    rel = [i for i in items if i.source_type == "reddit"]
    assert any("Hynix" in i.title for i in rel)
    assert all(i.reach_score >= 0 for i in rel)
    hynix = next(i for i in rel if "Hynix" in i.title)
    assert hynix.reach_score == 320.0
    assert hynix.published_at is not None
