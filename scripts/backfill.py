#!/usr/bin/env python3
"""백필 스크립트 (M5).

- 가격 백필: 테마 price_symbols 를 pykrx/yfinance 로 소급 수집 → price_history
- 재집계: 분류된 아이템을 다시 일별 집계

뉴스 백필 한계: Naver 검색 API 는 날짜 소급 파라미터가 없어 과거 소급이 제한적이다.
NewsAPI 는 from/to 파라미터로 소급 가능(유료 플랜 기간 제한). GDELT 는 장기 소급 가능
(별도 collector 로 M7 백테스트에서 확장). 초기 시계열은 scripts/seed_demo.py 로 채운다.

사용:
    python scripts/backfill.py --theme semiconductor --days 365 --prices
    python scripts/backfill.py --theme semiconductor --reaggregate
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sentiment_radar.config import load_theme  # noqa: E402
from sentiment_radar.db import get_db  # noqa: E402
from sentiment_radar.models import KST, utcnow_iso  # noqa: E402
from sentiment_radar.pipeline import compute_and_store  # noqa: E402
from sentiment_radar.prices import MarketDataProvider  # noqa: E402

log = logging.getLogger("backfill")


def backfill_prices(db, symbols: dict[str, str], days: int, provider=None) -> int:
    provider = provider or MarketDataProvider()
    end = datetime.now(KST).date()
    start = end - timedelta(days=days)
    now = utcnow_iso()
    total = 0
    for _, symbol in symbols.items():
        series = provider.get_series(symbol, start.isoformat(), end.isoformat())
        if not series:
            log.warning("[backfill] %s 시세 없음(네트워크/패키지 확인)", symbol)
            continue
        prev_close = None
        for d in sorted(series):
            close = series[d]
            ret = ((close - prev_close) / prev_close * 100) if prev_close else None
            db.upsert_price(symbol=symbol, bucket_date=d, close=close,
                            ret=round(ret, 3) if ret is not None else None,
                            collected_at=now)
            prev_close = close
            total += 1
        log.info("[backfill] %s: %d일 시세 저장", symbol, len(series))
    return total


def main() -> None:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", required=True)
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--prices", action="store_true", help="가격 백필")
    ap.add_argument("--reaggregate", action="store_true", help="분류 데이터 재집계")
    args = ap.parse_args()

    theme = load_theme(args.theme)
    with get_db() as db:
        if args.prices:
            n = backfill_prices(db, theme.price_symbols, args.days)
            log.info("가격 백필 완료: %d행", n)
        if args.reaggregate:
            summary = compute_and_store(db, theme.theme)
            log.info("재집계 완료: %d개 날짜", len(summary))
        if not (args.prices or args.reaggregate):
            log.info("옵션 없음 — --prices 또는 --reaggregate 를 지정하세요.")


if __name__ == "__main__":
    main()
