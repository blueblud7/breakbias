import pytest

from sentiment_radar.attention import DictTrendsProvider, collect_trends
from sentiment_radar.config import Theme
from sentiment_radar.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    d.init_schema()
    yield d
    d.close()


@pytest.fixture
def theme():
    return Theme(
        theme="semiconductor", display_name="반도체", my_view="none",
        keywords_ko=[], keywords_en=[], relevance_must_include_any=[],
        trends_pairs=[
            {"up": "반도체 매수", "down": "반도체 폭락"},
            {"up": "삼성전자 매수", "down": "삼성전자 매도"},
        ],
    )


def test_collect_trends_stores_raw_and_ratio(db, theme):
    provider = DictTrendsProvider({
        "반도체 매수": 75.0, "반도체 폭락": 25.0,   # 비율 0.75 (강세 관심)
        "삼성전자 매수": 20.0, "삼성전자 매도": 60.0,  # 비율 0.25 (약세 관심)
    })
    saved = collect_trends(db, theme, provider, as_of="2026-07-07")
    # 원시 4건 + 비율 2건
    assert saved == 6

    ratios = db.fetch_attention("semiconductor", metric="trends_ratio")
    by_kw = {r["keyword"]: r["value"] for r in ratios}
    assert by_kw["반도체 매수|반도체 폭락"] == pytest.approx(0.75)
    assert by_kw["삼성전자 매수|삼성전자 매도"] == pytest.approx(0.25)


def test_collect_trends_handles_missing_keyword(db, theme):
    # down 키워드 관심도 누락 → 해당 비율은 저장 안 함, 원시만
    provider = DictTrendsProvider({"반도체 매수": 50.0, "삼성전자 매수": 30.0,
                                   "삼성전자 매도": 30.0})
    saved = collect_trends(db, theme, provider, as_of="2026-07-07")
    ratios = db.fetch_attention("semiconductor", metric="trends_ratio")
    kws = {r["keyword"] for r in ratios}
    assert "반도체 매수|반도체 폭락" not in kws       # down 없음 → 비율 생략
    assert "삼성전자 매수|삼성전자 매도" in kws


def test_no_pairs_returns_zero(db):
    t = Theme(theme="x", display_name="x", my_view="none",
              keywords_ko=[], keywords_en=[], relevance_must_include_any=[],
              trends_pairs=[])
    assert collect_trends(db, t, DictTrendsProvider({}), as_of="2026-07-07") == 0
