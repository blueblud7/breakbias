import pytest

from sentiment_radar import dashboard_data as dd
from sentiment_radar.db import Database
from sentiment_radar.models import Classification, Item, utcnow_iso


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    d.init_schema()
    yield d
    d.close()


def _agg(db, theme, date, scope, nsi, **extra):
    m = {"n_items": extra.get("n", 10),
         "pct_pos_raw": 50, "pct_neu_raw": 20, "pct_neg_raw": 30, "nsi_raw": nsi,
         "pct_pos_wt": 50, "pct_neu_wt": 20, "pct_neg_wt": 30, "nsi_wt": nsi,
         "extreme_flag": extra.get("extreme", 0), "divergence": extra.get("div")}
    db.upsert_aggregate(theme, date, scope, m, utcnow_iso())


def _item(db, theme, title, sentiment, source_type="news_kr", reach=0.0):
    iid = db.insert_item(Item(theme=theme, source_type=source_type, title=title,
                              url=f"https://x/{abs(hash(title))}",
                              published_at=utcnow_iso(), reach_score=reach))
    db.insert_classification(Classification(item_id=iid, sentiment=sentiment,
                                            confidence=0.8, one_line_summary=title,
                                            model="t").to_row())


def test_gauge_delta(db):
    _agg(db, "t", "2026-07-06", "all", -30)
    _agg(db, "t", "2026-07-07", "all", -10, div=50)
    g = dd.get_today_gauge(db, "t", "2026-07-07")
    assert g["nsi_wt"] == -10
    assert g["delta_wt"] == 20.0        # -10 - (-30)
    assert g["divergence"] == 50


def test_gauge_falls_back_to_latest(db):
    _agg(db, "t", "2026-07-01", "all", 15)
    g = dd.get_today_gauge(db, "t", "2099-01-01")   # 없는 날짜 → 최신 폴백
    assert g["date"] == "2026-07-01"


def test_timeseries_ascending(db):
    _agg(db, "t", "2026-07-05", "all", 10)
    _agg(db, "t", "2026-07-07", "all", -5)
    _agg(db, "t", "2026-07-06", "all", 2)
    ts = dd.get_timeseries(db, "t", "all")
    assert [r["bucket_date"] for r in ts] == ["2026-07-05", "2026-07-06", "2026-07-07"]


def test_items_opposite_first_when_view_positive(db):
    # 내 뷰=positive → 부정 아이템이 먼저 와야 (확증편향 방지)
    _item(db, "t", "긍정기사", "positive")
    _item(db, "t", "부정기사", "negative")
    _item(db, "t", "중립기사", "neutral")
    items = dd.get_items(db, "t", my_view="positive")
    assert items[0]["sentiment"] == "negative"
    assert items[-1]["sentiment"] == "positive"


def test_items_opposite_first_ties_break_by_reach(db):
    _item(db, "t", "부정A", "negative", reach=100)
    _item(db, "t", "부정B", "negative", reach=9000)
    items = dd.get_items(db, "t", my_view="positive")
    # 같은 센티먼트면 reach 큰 것 우선
    assert items[0]["title"] == "부정B"


def test_source_matrix_pivot(db):
    _agg(db, "t", "2026-07-06", "youtube", -80)
    _agg(db, "t", "2026-07-07", "youtube", -60)
    _agg(db, "t", "2026-07-07", "report", 40)
    mat = dd.get_source_matrix(db, "t")
    assert set(mat["sources"]) == {"youtube", "report"}
    assert mat["nsi"]["youtube"]["2026-07-06"] == -80
    assert mat["nsi"]["report"]["2026-07-07"] == 40


def test_get_items_sentiment_filter(db):
    _item(db, "t", "긍정", "positive")
    _item(db, "t", "부정", "negative")
    only_neg = dd.get_items(db, "t", sentiment="negative")
    assert len(only_neg) == 1 and only_neg[0]["sentiment"] == "negative"
