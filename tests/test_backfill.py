import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from backfill import backfill_prices  # noqa: E402
from sentiment_radar.db import Database  # noqa: E402
from sentiment_radar.prices import DictPriceProvider  # noqa: E402


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    d.init_schema()
    yield d
    d.close()


def test_backfill_prices_computes_returns(db):
    provider = DictPriceProvider({"1001": {
        "2026-01-02": 2500.0, "2026-01-03": 2550.0, "2026-01-06": 2525.0,
    }})
    n = backfill_prices(db, {"kospi": "1001"}, days=3650, provider=provider)
    assert n == 3
    rows = db.fetch_price_series("1001", limit=10)
    assert [r["bucket_date"] for r in rows] == ["2026-01-02", "2026-01-03", "2026-01-06"]
    assert rows[0]["ret"] is None                      # 첫 날은 전일 없음
    assert rows[1]["ret"] == pytest.approx(2.0)        # 2500 -> 2550 = +2%
    assert rows[2]["ret"] == pytest.approx(-0.98, abs=0.02)


def test_backfill_empty_series_noop(db):
    n = backfill_prices(db, {"x": "^NONE"}, days=30, provider=DictPriceProvider({}))
    assert n == 0
