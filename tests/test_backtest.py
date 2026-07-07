import os

import pytest

from sentiment_radar import backtest as bt
from sentiment_radar.db import Database
from sentiment_radar.models import utcnow_iso


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    d.init_schema()
    yield d
    d.close()


def test_lag_correlation_peaks_at_true_lag():
    # ret[t] = nsi[t-2] → nsi 가 수익률에 lag +2 로 선행 → corr[+2] 가 최대
    nsi = [((i * 7) % 11) - 5 for i in range(30)]  # 비상수 시퀀스
    series = []
    for t in range(30):
        series.append({"nsi_wt": nsi[t], "ret": nsi[t - 2] if t >= 2 else None})
    lc = bt.lag_correlation(series, "nsi_wt", "ret", max_lag=5)
    best = max((k for k in lc if lc[k] is not None), key=lambda k: lc[k])
    assert best == 2
    assert lc[2] == pytest.approx(1.0, abs=1e-6)


def test_lag_correlation_handles_short_series():
    series = [{"nsi_wt": 1, "ret": 1}, {"nsi_wt": 2, "ret": 2}]
    lc = bt.lag_correlation(series, "nsi_wt", "ret", max_lag=3)
    assert lc[0] is None  # 표본 < 3 → None


def test_event_study_forward_return():
    close = [100, 100, 100, 110, 120, 120, 120, 120]
    series = [{"close": c, "ev": 1 if i == 2 else 0} for i, c in enumerate(close)]
    es = bt.event_study(series, "ev", "테스트", horizons=(2,))
    assert es.n_events == 1
    # +2일: (close[4]-close[2])/close[2] = (120-100)/100 = 20%
    assert es.horizons[2]["mean"] == pytest.approx(20.0)
    assert es.baseline[2]["n"] > 0


def test_mark_events_swing_and_extreme():
    # NSI 가 5일 만에 +10 → -40 (변화폭 50 ≥ 30) → 급변 이벤트
    series = [{"nsi_wt": 10, "extreme": 0}] * 5 + [{"nsi_wt": -40, "extreme": 1}]
    series = [dict(x) for x in series]  # 복사
    bt.mark_events(series, nsi_change_window=5, nsi_change_threshold=30)
    assert series[5]["event_swing"] == 1
    assert series[5]["event_extreme"] == 1
    assert series[0]["event_swing"] == 0


def _agg(db, theme, date, nsi, extreme=0, div=None):
    m = {"n_items": 5, "pct_pos_raw": 50, "pct_neu_raw": 20, "pct_neg_raw": 30,
         "nsi_raw": nsi, "pct_pos_wt": 50, "pct_neu_wt": 20, "pct_neg_wt": 30,
         "nsi_wt": nsi, "extreme_flag": extreme, "divergence": div}
    db.upsert_aggregate(theme, date, "all", m, utcnow_iso())


def test_build_joined_series(db):
    theme = "t"
    for d, nsi, close, ret in [
        ("2026-01-02", 10, 2500.0, None),
        ("2026-01-03", 25, 2550.0, 2.0),
        ("2026-01-06", -5, 2525.0, -0.98),
    ]:
        _agg(db, theme, d, nsi)
        db.upsert_price(symbol="1001", bucket_date=d, close=close, ret=ret,
                        collected_at=utcnow_iso())
    series = bt.build_joined_series(db, theme, "1001")
    assert [r["date"] for r in series] == ["2026-01-02", "2026-01-03", "2026-01-06"]
    assert series[0]["nsi_change"] is None
    assert series[1]["nsi_change"] == 15   # 25 - 10
    assert series[2]["close"] == 2525.0


def test_export_features_writes_file(db, tmp_path):
    theme = "t"
    for d, nsi, close in [("2026-01-02", 10, 2500.0), ("2026-01-03", 25, 2550.0)]:
        _agg(db, theme, d, nsi)
        db.upsert_price(symbol="1001", bucket_date=d, close=close, ret=None,
                        collected_at=utcnow_iso())
    out = bt.export_features(db, theme, "1001", str(tmp_path / "feat.parquet"))
    assert out is not None
    assert os.path.exists(out)


def test_export_features_empty_returns_none(db):
    assert bt.export_features(db, "none", "1001", "/tmp/x.parquet") is None
