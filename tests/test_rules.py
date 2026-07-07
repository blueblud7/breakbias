import pytest

from sentiment_radar.db import Database
from sentiment_radar.notify import Notifier
from sentiment_radar.rules import RuleEngine


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    d.init_schema()
    yield d
    d.close()


def _seed_agg(db, theme, date, scope="all", **cols):
    from sentiment_radar.models import utcnow_iso
    metrics = {
        "n_items": cols.get("n_items", 10),
        "pct_pos_raw": 0, "pct_neu_raw": 0, "pct_neg_raw": 0, "nsi_raw": 0,
        "pct_pos_wt": 0, "pct_neu_wt": 0, "pct_neg_wt": 0, "nsi_wt": 0,
        "extreme_flag": 0, "divergence": None,
    }
    metrics.update(cols)
    db.upsert_aggregate(theme, date, scope, metrics, utcnow_iso())


class Capture:
    def __init__(self):
        self.messages = []

    def __call__(self, msg):
        self.messages.append(msg)
        return True


def test_add_rule_writes_history(db):
    eng = RuleEngine(db)
    rid = eng.add_rule(name="극단 약세", condition={
        "metric": "nsi_wt", "scope": "all", "op": "<=", "value": -60}, theme="t")
    hist = db.fetch_rule_history(rid)
    assert len(hist) == 1
    assert hist[0]["change_type"] == "create"


def test_update_rule_appends_history_snapshot(db):
    eng = RuleEngine(db)
    rid = eng.add_rule(name="r", condition={
        "metric": "nsi_wt", "op": "<=", "value": -60})
    eng.update_rule(rid, condition={"metric": "nsi_wt", "op": "<=", "value": -50})
    hist = db.fetch_rule_history(rid)
    assert [h["change_type"] for h in hist] == ["create", "update"]
    # 스냅샷에 변경 전/후 값이 남아 사후 변경을 볼 수 있다
    import json
    snap_after = json.loads(hist[1]["snapshot_json"])
    assert "-50" in snap_after["condition_json"]


def test_invalid_metric_rejected(db):
    eng = RuleEngine(db)
    with pytest.raises(ValueError):
        eng.add_rule(name="bad", condition={
            "metric": "해킹", "op": "<=", "value": 0})


def test_evaluate_simple_threshold(db):
    eng = RuleEngine(db)
    rid = eng.add_rule(name="괴리", condition={
        "metric": "divergence", "scope": "all", "op": ">=", "value": 30})
    rule = db.fetch_rule(rid)
    _seed_agg(db, "t", "2026-01-10", divergence=25)   # 미충족
    assert eng.evaluate(rule, "t", as_of="2026-01-10") is False
    _seed_agg(db, "t", "2026-01-11", divergence=40)   # 충족
    assert eng.evaluate(rule, "t", as_of="2026-01-11") is True


def test_evaluate_consecutive_days(db):
    eng = RuleEngine(db)
    rid = eng.add_rule(name="3일연속", condition={
        "metric": "nsi_wt", "scope": "all", "op": "<=", "value": -60,
        "consecutive_days": 3})
    rule = db.fetch_rule(rid)
    _seed_agg(db, "t", "2026-01-10", nsi_wt=-70)
    _seed_agg(db, "t", "2026-01-11", nsi_wt=-40)   # 중간에 완화
    _seed_agg(db, "t", "2026-01-12", nsi_wt=-65)
    assert eng.evaluate(rule, "t", as_of="2026-01-12") is False
    _seed_agg(db, "t", "2026-01-13", nsi_wt=-80)
    _seed_agg(db, "t", "2026-01-14", nsi_wt=-61)
    # 12,13,14 모두 <= -60 → 충족
    assert eng.evaluate(rule, "t", as_of="2026-01-14") is True


def test_check_and_notify_fires_with_provenance(db):
    cap = Capture()
    eng = RuleEngine(db, Notifier(send_fn=cap))
    rid = eng.add_rule(name="극단 약세", condition={
        "metric": "nsi_wt", "scope": "all", "op": "<=", "value": -60},
        action_text="역발상 검토", theme="t")
    _seed_agg(db, "t", "2026-01-11", nsi_wt=-70)
    fired = eng.check_and_notify("t", as_of="2026-01-11")
    assert fired == [rid]
    assert len(cap.messages) == 1
    msg = cap.messages[0]
    assert "극단 약세" in msg
    assert "역발상 검토" in msg
    assert "당신이" in msg                     # 등록 당시 원문 + 등록일 표기
    # last_triggered_at 갱신됨
    assert db.fetch_rule(rid)["last_triggered_at"] is not None


def test_theme_scoped_rule_ignored_for_other_theme(db):
    cap = Capture()
    eng = RuleEngine(db, Notifier(send_fn=cap))
    eng.add_rule(name="반도체전용", condition={
        "metric": "nsi_wt", "op": "<=", "value": -60}, theme="semiconductor")
    _seed_agg(db, "battery", "2026-01-11", nsi_wt=-90)
    fired = eng.check_and_notify("battery", as_of="2026-01-11")
    assert fired == []                         # 다른 테마 규칙은 발동 안 함
