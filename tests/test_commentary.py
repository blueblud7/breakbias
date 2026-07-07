import json

import pytest

from sentiment_radar.db import Database
from sentiment_radar.llm.commentary import CommentaryGenerator, parse_commentary
from sentiment_radar.models import Classification, Item, utcnow_iso


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    d.init_schema()
    yield d
    d.close()


def _agg(db, theme, date, scope, nsi, extreme=0, div=None):
    m = {"n_items": 10, "pct_pos_raw": 50, "pct_neu_raw": 20, "pct_neg_raw": 30,
         "nsi_raw": nsi, "pct_pos_wt": 50, "pct_neu_wt": 20, "pct_neg_wt": 30,
         "nsi_wt": nsi, "extreme_flag": extreme, "divergence": div}
    db.upsert_aggregate(theme, date, scope, m, utcnow_iso())


def _classified(db, theme, title, sentiment, summary, date_iso):
    iid = db.insert_item(Item(theme=theme, source_type="news_kr", title=title,
                              url=f"https://x/{abs(hash(title))}", published_at=date_iso))
    db.insert_classification(Classification(item_id=iid, sentiment=sentiment,
                                            confidence=0.8, one_line_summary=summary,
                                            model="t").to_row())


# ---------- 파싱 ----------

def test_parse_commentary_json():
    r = parse_commentary(json.dumps({
        "commentary": "약세 전환",
        "counter_arguments": [{"claim": "c1", "basis": "b1"},
                              {"claim": "c2", "basis": "b2"},
                              {"claim": "c3", "basis": "b3"}],
        "next_events": ["실적 발표"],
    }, ensure_ascii=False))
    assert r["commentary"] == "약세 전환"
    assert len(r["counter_arguments"]) == 3
    assert r["next_events"] == ["실적 발표"]


def test_parse_commentary_truncates_to_three():
    r = parse_commentary(json.dumps({
        "commentary": "x",
        "counter_arguments": [{"claim": f"c{i}"} for i in range(5)],
    }))
    assert len(r["counter_arguments"]) == 3


def test_parse_commentary_non_json_fallback():
    r = parse_commentary("그냥 자유 서술 총평입니다.")
    assert "자유 서술" in r["commentary"]
    assert r["counter_arguments"] == []


def test_parse_commentary_code_fence():
    r = parse_commentary('```json\n{"commentary":"본문","counter_arguments":[]}\n```')
    assert r["commentary"] == "본문"


# ---------- 생성 ----------

def test_generate_stores_commentary(db):
    date = "2026-07-07"
    _agg(db, "semiconductor", date, "all", -45, extreme=0, div=80)
    _agg(db, "semiconductor", date, "institutional", 20)
    _agg(db, "semiconductor", date, "retail", -60)
    _classified(db, "semiconductor", "반도체 더 떨어질 것", "negative", "추가 하락 전망",
                f"{date}T09:00:00+00:00")
    _classified(db, "semiconductor", "반도체 반등 신호", "positive", "저점 매수 논거",
                f"{date}T09:00:00+00:00")

    captured = {}

    def fake_complete(system, user):
        captured["user"] = user
        return json.dumps({
            "commentary": "리테일 약세가 심화됐다.",
            "counter_arguments": [{"claim": "과매도", "basis": "저점 매수 논거"},
                                  {"claim": "수급 견조", "basis": "리포트"},
                                  {"claim": "관심 저점", "basis": "trends"}],
            "next_events": ["메모리 고정가"],
        }, ensure_ascii=False), 200, 120

    gen = CommentaryGenerator(model="deepseek-v4-pro", complete_fn=fake_complete)
    res = gen.generate(db, "semiconductor", date)
    assert res is not None
    assert len(res["counter_arguments"]) == 3
    # 컨텍스트에 대표 의견이 실렸는지
    assert "저점 매수 논거" in captured["user"]
    assert "기관 NSI" in captured["user"]

    # DB 저장 확인
    row = db.fetch_commentary("semiconductor", date)
    assert row is not None
    assert "리테일 약세" in row["commentary"]
    cas = json.loads(row["counter_args"])
    assert len(cas) == 3


def test_generate_skips_without_aggregate(db):
    gen = CommentaryGenerator(model="x", complete_fn=lambda s, u: ("{}", 1, 1))
    assert gen.generate(db, "semiconductor", "2099-01-01") is None
