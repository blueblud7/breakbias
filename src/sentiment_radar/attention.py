"""Google Trends 관심도(attention) 트랙 (M3).

검색량은 센티먼트가 아니라 '관심도' 지표이므로 items/집계와 분리해
attention_metrics 테이블에 별도 저장한다.

방향성 키워드 쌍(예: "반도체 매수" vs "반도체 폭락")의 상대 비율을 보조 지표로 계산:
  trends_ratio = up / (up + down)   (0~1, >0.5 = 강세 관심 우위)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol

from .config import Theme
from .models import KST, utcnow_iso

log = logging.getLogger(__name__)


def _kst_today() -> str:
    return datetime.now(KST).date().isoformat()


class TrendsProvider(Protocol):
    def interest(self, keywords: list[str]) -> dict[str, float]:
        """키워드별 최신 관심도 값 (0~100). 실패 시 빈 dict."""
        ...


class PytrendsProvider:
    def __init__(self, geo: str = "KR", timeframe: str = "now 7-d") -> None:
        self.geo = geo
        self.timeframe = timeframe

    def interest(self, keywords: list[str]) -> dict[str, float]:
        if not keywords:
            return {}
        try:
            from pytrends.request import TrendReq
            pt = TrendReq(hl="ko-KR", tz=540)
            # pytrends 는 최대 5개 키워드
            out: dict[str, float] = {}
            for i in range(0, len(keywords), 5):
                chunk = keywords[i : i + 5]
                pt.build_payload(chunk, timeframe=self.timeframe, geo=self.geo)
                df = pt.interest_over_time()
                if df is None or df.empty:
                    continue
                for kw in chunk:
                    if kw in df.columns:
                        out[kw] = float(df[kw].iloc[-1])
            return out
        except Exception as e:  # 네트워크/레이트리밋 방어
            log.error("[trends] 조회 실패: %s", e)
            return {}


class DictTrendsProvider:
    """테스트용: {keyword: value}."""

    def __init__(self, data: dict[str, float]) -> None:
        self._data = dict(data)

    def interest(self, keywords: list[str]) -> dict[str, float]:
        return {k: self._data[k] for k in keywords if k in self._data}


def collect_trends(db, theme: Theme, provider: TrendsProvider | None = None,
                   as_of: str | None = None) -> int:
    """테마의 trends 키워드/쌍 관심도를 수집해 attention_metrics 저장. 저장 건수 반환."""
    provider = provider or PytrendsProvider()
    as_of = as_of or _kst_today()
    now = utcnow_iso()

    # 개별 키워드 + 쌍 구성요소를 한 번에 조회
    all_kw: list[str] = []
    for pair in theme.trends_pairs:
        all_kw += [pair.get("up", ""), pair.get("down", "")]
    all_kw = [k for k in dict.fromkeys(all_kw) if k]  # 중복 제거
    if not all_kw:
        log.info("[trends] '%s' trends_pairs 미설정 — 스킵", theme.theme)
        return 0

    values = provider.interest(all_kw)
    saved = 0

    # 원시 관심도 저장
    for kw, val in values.items():
        db.upsert_attention(theme=theme.theme, bucket_date=as_of,
                            metric="google_trends", keyword=kw, value=val,
                            collected_at=now)
        saved += 1

    # 방향성 비율 저장
    for pair in theme.trends_pairs:
        up_kw, down_kw = pair.get("up"), pair.get("down")
        up, down = values.get(up_kw), values.get(down_kw)
        if up is None or down is None:
            continue
        ratio = up / (up + down) if (up + down) > 0 else None
        db.upsert_attention(theme=theme.theme, bucket_date=as_of,
                            metric="trends_ratio", keyword=f"{up_kw}|{down_kw}",
                            value=ratio, collected_at=now)
        saved += 1
        log.info("[trends] %s 비율 %.2f (관심 우위: %s)",
                 f"{up_kw}|{down_kw}", ratio or 0,
                 "강세" if (ratio or 0) > 0.5 else "약세")
    return saved
