import pytest

from sentiment_radar.db import Database
from sentiment_radar.predictions import PredictionJournal
from sentiment_radar.prices import DictPriceProvider


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    d.init_schema()
    yield d
    d.close()


# 코스피(1001) 가격: 상승 시나리오 & 하락 시나리오 날짜 준비
PRICES = DictPriceProvider({
    "1001": {
        "2026-01-01": 2500.0,
        "2026-01-15": 2600.0,   # +4% (2주 뒤)
        "2026-02-01": 2400.0,   # 하락
    },
    "^SOX": {
        "2026-01-01": 5000.0,
        "2026-01-15": 4800.0,   # -4%
    },
})


def test_add_sets_entry_and_resolve_date(db):
    j = PredictionJournal(db, PRICES)
    pid = j.add(theme="semiconductor", my_view="positive", confidence=70,
                horizon_days=14, basis="HBM", target_symbol="1001",
                entry_date="2026-01-01")
    row = db.fetch_predictions("semiconductor")[0]
    assert row["id"] == pid
    assert row["entry_close"] == 2500.0
    assert row["resolve_date"] == "2026-01-15"
    assert row["outcome"] == "pending"


def test_add_rejects_bad_view_and_confidence(db):
    j = PredictionJournal(db, PRICES)
    with pytest.raises(ValueError):
        j.add(theme="t", my_view="neutral", confidence=70, horizon_days=14,
              basis="", target_symbol="1001")
    with pytest.raises(ValueError):
        j.add(theme="t", my_view="positive", confidence=30, horizon_days=14,
              basis="", target_symbol="1001")


def test_resolve_hit_positive(db):
    j = PredictionJournal(db, PRICES)
    j.add(theme="semiconductor", my_view="positive", confidence=80,
          horizon_days=14, basis="", target_symbol="1001", entry_date="2026-01-01")
    resolved = j.resolve_due(as_of="2026-01-20")
    assert len(resolved) == 1
    r = resolved[0]
    assert r["outcome"] == "hit"                 # +4% & positive
    assert round(r["actual_return"], 1) == 4.0
    # Brier = (0.8 - 1)^2 = 0.04
    assert round(r["brier"], 3) == 0.04


def test_resolve_miss_negative(db):
    j = PredictionJournal(db, PRICES)
    j.add(theme="semiconductor", my_view="negative", confidence=90,
          horizon_days=14, basis="", target_symbol="1001", entry_date="2026-01-01")
    resolved = j.resolve_due(as_of="2026-01-20")
    r = resolved[0]
    assert r["outcome"] == "miss"                # 지수 올랐는데 negative 예측
    # Brier = (0.9 - 0)^2 = 0.81
    assert round(r["brier"], 3) == 0.81


def test_resolve_skips_not_due(db):
    j = PredictionJournal(db, PRICES)
    j.add(theme="t", my_view="positive", confidence=70, horizon_days=14,
          basis="", target_symbol="1001", entry_date="2026-01-01")
    # 만기(2026-01-15) 전 날짜로 판정 시도 → 아무 것도 판정 안 됨
    assert j.resolve_due(as_of="2026-01-10") == []


def test_brier_summary_and_hit_rate(db):
    j = PredictionJournal(db, PRICES)
    # 적중 1건(positive, +4%), 빗나감 1건(negative, +4%)
    j.add(theme="semiconductor", my_view="positive", confidence=80,
          horizon_days=14, basis="", target_symbol="1001", entry_date="2026-01-01")
    j.add(theme="semiconductor", my_view="negative", confidence=60,
          horizon_days=14, basis="", target_symbol="1001", entry_date="2026-01-01")
    j.resolve_due(as_of="2026-01-20")
    s = j.brier_summary("semiconductor")
    assert s.n == 2
    assert s.hit_rate == 0.5
    # Brier 평균 = (0.04 + 0.36)/2 = 0.20
    assert round(s.brier_mean, 3) == 0.20


def test_calibration_bins(db):
    j = PredictionJournal(db, PRICES)
    j.add(theme="semiconductor", my_view="positive", confidence=85,
          horizon_days=14, basis="", target_symbol="1001", entry_date="2026-01-01")
    j.resolve_due(as_of="2026-01-20")
    cal = j.calibration("semiconductor")
    bin_80_90 = next(c for c in cal if c["bin"] == "80-90")
    assert bin_80_90["n"] == 1
    assert bin_80_90["actual_hit_rate"] == 1.0        # 적중
    assert round(bin_80_90["avg_confidence"], 2) == 0.85
