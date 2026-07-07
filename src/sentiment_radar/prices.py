"""가격 조회 — 예측 판정(M6) 및 백테스트(M7)용.

symbol 규칙:
  - 숫자코드(예: "1001") 또는 "kospi"/"kosdaq" → pykrx 지수
  - 그 외("^SOX", "AAPL" 등) → yfinance

네트워크/패키지 실패는 조용히 None 을 반환한다 (판정은 나중에 재시도 가능).
특정 날짜가 휴장이면 그 날짜 '이전' 최근 종가를 쓴다.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Protocol

log = logging.getLogger(__name__)

_KOSPI_ALIASES = {"kospi": "1001", "kosdaq": "2001"}


class PriceProvider(Protocol):
    def get_close(self, symbol: str, on_or_before: str) -> float | None:
        """on_or_before(YYYY-MM-DD) 시점 또는 그 이전 최근 종가."""
        ...


class MarketDataProvider:
    """pykrx + yfinance 실제 조회 구현."""

    def get_close(self, symbol: str, on_or_before: str) -> float | None:
        try:
            target = datetime.strptime(on_or_before, "%Y-%m-%d").date()
        except ValueError:
            return None

        code = _KOSPI_ALIASES.get(symbol.lower(), symbol)
        if code.isdigit():
            return self._pykrx_close(code, target)
        return self._yf_close(symbol, target)

    def _pykrx_close(self, code: str, target: date) -> float | None:
        try:
            from pykrx import stock
        except ImportError:
            log.warning("[prices] pykrx 미설치 — %s 조회 불가", code)
            return None
        start = (target - timedelta(days=10)).strftime("%Y%m%d")
        end = target.strftime("%Y%m%d")
        try:
            df = stock.get_index_ohlcv(start, end, code)
        except Exception as e:  # 네트워크/파라미터 방어
            log.error("[prices] pykrx %s 실패: %s", code, e)
            return None
        if df is None or df.empty:
            return None
        return float(df["종가"].iloc[-1])

    def _yf_close(self, symbol: str, target: date) -> float | None:
        try:
            import yfinance as yf
        except ImportError:
            log.warning("[prices] yfinance 미설치 — %s 조회 불가", symbol)
            return None
        start = (target - timedelta(days=10)).strftime("%Y-%m-%d")
        end = (target + timedelta(days=1)).strftime("%Y-%m-%d")
        try:
            df = yf.download(symbol, start=start, end=end, progress=False,
                             auto_adjust=True)
        except Exception as e:
            log.error("[prices] yfinance %s 실패: %s", symbol, e)
            return None
        if df is None or df.empty:
            return None
        return float(df["Close"].iloc[-1])


class DictPriceProvider:
    """테스트/백필용: {(symbol, date): close} 또는 {symbol: {date: close}}."""

    def __init__(self, data: dict) -> None:
        # 평탄화: {(symbol, 'YYYY-MM-DD'): close}
        self._flat: dict[tuple[str, str], float] = {}
        for k, v in data.items():
            if isinstance(v, dict):
                for d, c in v.items():
                    self._flat[(k, d)] = float(c)
            else:
                self._flat[k] = float(v)

    def get_close(self, symbol: str, on_or_before: str) -> float | None:
        # on_or_before 이하 날짜 중 가장 최근
        candidates = sorted(
            (d for (s, d) in self._flat if s == symbol and d <= on_or_before)
        )
        if not candidates:
            return None
        return self._flat[(symbol, candidates[-1])]
