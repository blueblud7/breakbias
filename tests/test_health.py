from datetime import datetime, timedelta

import pytest

from sentiment_radar.db import Database
from sentiment_radar.health import cost_summary, health_report
from sentiment_radar.models import KST, Item, utcnow_iso


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    d.init_schema()
    yield d
    d.close()


def _agg(db, theme, date):
    m = {"n_items": 5, "pct_pos_raw": 50, "pct_neu_raw": 20, "pct_neg_raw": 30,
         "nsi_raw": 20, "pct_pos_wt": 50, "pct_neu_wt": 20, "pct_neg_wt": 30,
         "nsi_wt": 20, "extreme_flag": 0, "divergence": None}
    db.upsert_aggregate(theme, date, "all", m, utcnow_iso())


def test_health_all_ok(db):
    theme = "semiconductor"
    db.insert_item(Item(theme=theme, source_type="news_kr", title="t",
                        url="https://x/1", collected_at=utcnow_iso()))
    today = datetime.now(KST).date()
    for i in range(3):
        _agg(db, theme, (today - timedelta(days=i)).isoformat())
    rep = health_report(db, theme)
    assert rep.ok
    assert all(c.ok for c in rep.checks)


def test_health_flags_missing_aggregates(db):
    theme = "semiconductor"
    db.insert_item(Item(theme=theme, source_type="news_kr", title="t",
                        url="https://x/1", collected_at=utcnow_iso()))
    # 오늘 집계만 있고 어제/그제 누락
    _agg(db, theme, datetime.now(KST).date().isoformat())
    rep = health_report(db, theme)
    cov = next(c for c in rep.checks if c.name == "일별 집계 커버리지")
    assert cov.ok is False
    assert "누락" in cov.detail


def test_health_flags_no_collection(db):
    rep = health_report(db, "semiconductor")
    fresh = next(c for c in rep.checks if c.name == "수집 이력")
    assert fresh.ok is False


def test_cost_summary(db):
    today = datetime.now(KST).date().isoformat()
    db.add_cost_log(bucket_date=today, model="gpt-5-nano", call_type="classify",
                    n_calls=10, prompt_tokens=1000, completion_tokens=500, cost_usd=0.05)
    db.add_cost_log(bucket_date=today, model="deepseek-v4-pro", call_type="commentary",
                    n_calls=1, prompt_tokens=800, completion_tokens=300, cost_usd=0.02)
    cs = cost_summary(db, days=30)
    assert cs["total_usd"] == pytest.approx(0.07)
    assert cs["today_usd"] == pytest.approx(0.07)
    models = {m["model"] for m in cs["by_model"]}
    assert models == {"gpt-5-nano", "deepseek-v4-pro"}
