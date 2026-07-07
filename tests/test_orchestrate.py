import json

import pytest

from sentiment_radar.db import Database
from sentiment_radar.llm.classifier import Classifier
from sentiment_radar.llm.commentary import CommentaryGenerator
from sentiment_radar.models import Item, utcnow_iso
from sentiment_radar.notify import Notifier
from sentiment_radar.orchestrate import run_full_pipeline
from sentiment_radar.prices import DictPriceProvider


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    d.init_schema()
    yield d
    d.close()


class FakeCollector:
    source_type = "news_kr"

    def __init__(self, titles):
        self.titles = titles

    def collect(self, theme):
        return [Item(theme=theme.theme, source_type="news_kr", title=t,
                     url=f"https://x/{abs(hash(t))}", published_at=utcnow_iso())
                for t in self.titles]


def _fake_classify(system, user):
    b = {"sentiment": "positive", "confidence": 0.8, "is_opinion": True} \
        if "오를" in user else {"sentiment": "negative", "confidence": 0.8, "is_opinion": True}
    return json.dumps(b, ensure_ascii=False), 30, 10


def _fake_commentary(system, user):
    return json.dumps({"commentary": "총평", "counter_arguments": [
        {"claim": "a", "basis": "1"}, {"claim": "b", "basis": "2"},
        {"claim": "c", "basis": "3"}]}, ensure_ascii=False), 100, 50


def test_full_pipeline_happy_path(db):
    cap = []
    rep = run_full_pipeline(
        "semiconductor", db=db,
        collector_instances=[FakeCollector(["반도체 오를 것", "반도체 더 떨어질 것"])],
        classifier=Classifier(model="gpt-5-nano", complete_fn=_fake_classify),
        commentary_gen=CommentaryGenerator(model="deepseek-v4-pro", complete_fn=_fake_commentary),
        price_provider=DictPriceProvider({}),
        notifier=Notifier(send_fn=lambda m: cap.append(m) or True),
    )
    assert rep.collected == 2
    assert rep.classified == 2
    assert rep.aggregated_dates >= 1
    assert rep.commentary is True
    assert rep.cost_usd > 0
    assert rep.errors == []
    assert cap == []                      # 실패 없음 → 알림 없음
    # 총평 저장 확인
    assert db.fetch_commentary("semiconductor") is not None


def test_stage_isolation_and_alert(db):
    """한 단계(수집)가 터져도 나머지는 진행되고 실패 알림이 발송된다."""
    class BoomCollector:
        source_type = "news_kr"
        def collect(self, theme):
            raise RuntimeError("수집 폭발")

    cap = []
    rep = run_full_pipeline(
        "semiconductor", db=db,
        collector_instances=[BoomCollector()],
        classifier=Classifier(model="gpt-5-nano", complete_fn=_fake_classify),
        do_commentary=False,
        notifier=Notifier(send_fn=lambda m: cap.append(m) or True),
    )
    assert any("collect" in e for e in rep.errors)
    assert rep.aggregated_dates == 0      # 수집 실패 → 집계할 것 없음(정상 진행)
    assert len(cap) == 1                   # 실패 알림 1건
    assert "실패" in cap[0]


def test_predictions_resolved_in_pipeline(db):
    prices = DictPriceProvider({"1001": {"2026-01-01": 2500.0, "2026-01-15": 2600.0}})
    # 만기 도래한 예측 선등록
    from sentiment_radar.predictions import PredictionJournal
    PredictionJournal(db, prices).add(
        theme="semiconductor", my_view="positive", confidence=70, horizon_days=14,
        basis="", target_symbol="1001", entry_date="2026-01-01")

    rep = run_full_pipeline(
        "semiconductor", db=db, do_collect=False, do_commentary=False,
        classifier=Classifier(model="x", complete_fn=_fake_classify),
        price_provider=prices,
        notifier=Notifier(send_fn=lambda m: True),
    )
    assert rep.resolved_predictions == 1
    assert db.fetch_predictions("semiconductor")[0]["outcome"] == "hit"
