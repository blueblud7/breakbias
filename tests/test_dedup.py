from sentiment_radar.models import Item
from sentiment_radar.pipeline.dedup import dedup_items


def _mk(title, url, source_type="news_kr"):
    it = Item(theme="t", source_type=source_type, title=title, url=url)
    return it


def test_dedup_exact_url():
    items = [
        _mk("삼성전자 반등", "https://a.com/1?utm_source=x"),
        _mk("삼성전자 반등 (재게시)", "https://www.a.com/1/"),  # 동일 URL 정규화
    ]
    kept = dedup_items(items, threshold=90)
    assert len(kept) == 1


def test_dedup_similar_title():
    items = [
        _mk("SK하이닉스 HBM 수요 급증 전망", "https://a.com/1"),
        _mk("SK하이닉스 HBM 수요 급증 전망!!", "https://b.com/2"),  # 유사 제목
    ]
    kept = dedup_items(items, threshold=90)
    assert len(kept) == 1


def test_dedup_keeps_distinct():
    items = [
        _mk("삼성전자 목표주가 상향", "https://a.com/1"),
        _mk("코스피 외국인 순매도 지속", "https://b.com/2"),
    ]
    kept = dedup_items(items, threshold=90)
    assert len(kept) == 2


def test_dedup_respects_existing_hashes():
    a = _mk("반도체 업황 바닥 통과", "https://a.com/1")
    existing = {a.content_hash or ""}
    # content_hash 를 강제로 채워 비교
    from sentiment_radar.utils.text import content_hash
    existing = {content_hash(a.title, a.url)}
    kept = dedup_items([a], existing_hashes=existing, threshold=90)
    assert len(kept) == 0
