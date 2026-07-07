"""M2 통합 테스트: 수집물 → 분류 → 집계 전체 사이클 (실제 API 없이).

FakeClassifier 는 제목의 어미로 '사실보도 vs 전망'을 흉내낸다:
  - "폭락했다" 류 → neutral, is_opinion=False (집계 제외)
  - "떨어질 것"  류 → negative, is_opinion=True
  - "오를 것"    류 → positive, is_opinion=True
"""

import json

import pytest

from sentiment_radar.config import Theme
from sentiment_radar.db import Database
from sentiment_radar.llm.classifier import Classifier
from sentiment_radar.models import Item, to_kst_date, utcnow_iso
from sentiment_radar.pipeline import compute_and_store, run_classification


CFG = {
    "source_weights": {"report": 3.0, "news_kr": 2.0, "blog": 1.0},
    "source_groups": {
        "institutional": ["report", "news_kr", "news_global"],
        "retail": ["blog", "youtube", "reddit", "telegram"],
    },
    "aggregation": {
        "extreme_threshold": 0.75, "min_confidence": 0.0, "exclude_non_opinion": True,
    },
}


def fake_complete(system, user):
    """user 프롬프트의 제목을 보고 규칙적으로 분류 JSON 을 만든다."""
    if "폭락했다" in user or "급등했다" in user:
        body = {"sentiment": "neutral", "confidence": 0.9, "is_opinion": False,
                "one_line_summary": "가격 등락 사실보도", "key_argument": "중계",
                "time_horizon": "unclear"}
    elif "떨어질" in user:
        body = {"sentiment": "negative", "confidence": 0.8, "is_opinion": True,
                "one_line_summary": "추가 하락 전망", "key_argument": "수요 둔화",
                "time_horizon": "mid"}
    elif "오를" in user or "상향" in user:
        body = {"sentiment": "positive", "confidence": 0.85, "is_opinion": True,
                "one_line_summary": "상승 전망", "key_argument": "HBM 수요",
                "time_horizon": "mid"}
    else:
        body = {"sentiment": "neutral", "confidence": 0.5, "is_opinion": True}
    return json.dumps(body, ensure_ascii=False), 50, 20


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
    )


def _add(db, title, source_type="news_kr", reach=0.0):
    it = Item(theme="semiconductor", source_type=source_type, title=title,
              url=f"https://x.com/{abs(hash(title))}", published_at=utcnow_iso(),
              reach_score=reach)
    return db.insert_item(it)


def test_full_cycle_classify_and_aggregate(db, theme):
    _add(db, "삼성전자 폭락했다")          # 사실보도 → 제외
    _add(db, "반도체 더 떨어질 것")        # 전망 negative
    _add(db, "HBM 수요로 주가 오를 것")    # 전망 positive
    _add(db, "SK하이닉스 목표주가 상향")   # 전망 positive

    clf = Classifier(model="gpt-5-nano", complete_fn=fake_complete)
    res = run_classification(db, theme, clf)
    assert res.total == 4
    assert res.classified == 4
    assert res.failed == 0
    assert res.cost_usd > 0.0            # 비용 로깅됨

    summary = compute_and_store(db, theme.theme, CFG)
    today = to_kst_date(utcnow_iso())
    assert today in summary

    rows = db.fetch_aggregates(theme.theme, "all", limit=5)
    assert len(rows) == 1
    agg = rows[0]
    # 사실보도 1건 제외 → 집계 대상 3건 (부정1, 긍정2)
    assert agg["n_items"] == 3
    assert round(agg["pct_pos_raw"], 1) == 66.7
    assert round(agg["pct_neg_raw"], 1) == 33.3
    assert agg["nsi_raw"] > 0


def test_cost_logged_and_budget_query(db, theme):
    _add(db, "반도체 오를 것")
    clf = Classifier(model="gpt-5-nano", complete_fn=fake_complete)
    run_classification(db, theme, clf)
    spent = db.today_cost_usd(to_kst_date(utcnow_iso()))
    assert spent > 0.0


def test_reclassify_is_idempotent(db, theme):
    _add(db, "반도체 오를 것")
    clf = Classifier(model="gpt-5-nano", complete_fn=fake_complete)
    run_classification(db, theme, clf)
    # 두 번째 실행 시 이미 분류된 것은 미분류 목록에 없음
    res2 = run_classification(db, theme, clf)
    assert res2.total == 0
