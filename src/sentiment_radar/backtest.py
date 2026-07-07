"""센티먼트-수익률 검증 모듈 (M7).

논조 변화가 실제 등락에 '선행'하는지 과거 데이터로 검증한다.

분석 3종:
  1) 시차 상관 + Granger 인과 — NSI(및 변화율/괴리) ↔ 지수 수익률, lag -N~+N
  2) 이벤트 스터디 — 급변점/극단 쏠림 이후 +5/+20일 수익률 분포 vs 기준선
  3) WFO 피처 export — sentiment_features.parquet 일별 출력

[해석 가이드]  ★ 코드/대시보드에 명시 ★
- 센티먼트 '수준'은 가격과 동행/후행하는 것이 일반적이다.
- 예측력(선행성)은 주로 '극단값'과 '변화율'에서만 기대할 수 있다.
- 통계적으로 유의하지 않으면 "선행성 없음"이 정상 결과이며, 이 역시 가치 있는 발견이다.
- lag > 0 = 센티먼트가 수익률에 선행(예측력 시사), lag < 0 = 수익률이 센티먼트에 선행(후행).
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

INTERPRETATION_GUIDE = (
    "센티먼트 '수준'은 가격과 동행/후행이 일반적이며, 예측력은 주로 극단값과 변화율에서만 "
    "기대할 수 있다. 통계적으로 유의하지 않으면 '선행성 없음'이 정상 결과이고 이 역시 "
    "가치 있는 발견이다. (lag>0=센티먼트 선행/예측력, lag<0=가격 선행/후행)"
)


def build_joined_series(db, theme: str, symbol: str) -> list[dict[str, Any]]:
    """집계(all) + 가격을 날짜로 정렬·조인. nsi_change/divergence 포함."""
    aggs = {r["bucket_date"]: r for r in db.fetch_aggregates(theme, "all", limit=1000)}
    prices = {r["bucket_date"]: r for r in db.fetch_price_series(symbol, limit=2000)}
    dates = sorted(set(aggs) & set(prices))

    out: list[dict[str, Any]] = []
    prev_nsi = None
    for d in dates:
        a, p = aggs[d], prices[d]
        nsi = a["nsi_wt"]
        out.append({
            "date": d,
            "nsi_wt": nsi,
            "nsi_change": (nsi - prev_nsi) if prev_nsi is not None else None,
            "divergence": a["divergence"],
            "extreme": int(a["extreme_flag"] or 0),
            "close": p["close"],
            "ret": p["ret"],
        })
        prev_nsi = nsi
    return out


def _corr(xs: list[float], ys: list[float]) -> float | None:
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    xv = [p[0] for p in pairs]
    yv = [p[1] for p in pairs]
    try:
        return round(statistics.correlation(xv, yv), 4)
    except statistics.StatisticsError:  # 분산 0 등
        return None


def lag_correlation(series: list[dict], x_key: str = "nsi_wt",
                    y_key: str = "ret", max_lag: int = 10) -> dict[int, float | None]:
    """lag 별 상관. corr[k] = corr(x[t], y[t+k]). k>0 이면 x 가 y 에 선행."""
    x = [r.get(x_key) for r in series]
    y = [r.get(y_key) for r in series]
    n = len(series)
    result: dict[int, float | None] = {}
    for k in range(-max_lag, max_lag + 1):
        xs, ys = [], []
        for t in range(n):
            tk = t + k
            if 0 <= tk < n:
                xs.append(x[t])
                ys.append(y[tk])
        result[k] = _corr(xs, ys)
    return result


def granger_pvalues(series: list[dict], x_key: str = "nsi_change",
                    y_key: str = "ret", max_lag: int = 5) -> dict[int, float]:
    """Granger 인과 검정 (x → y). statsmodels 필요, 없으면 빈 dict.

    귀무가설: x 는 y 를 Granger-인과하지 않는다. p<0.05 면 인과(선행) 시사.
    """
    rows = [(r.get(y_key), r.get(x_key)) for r in series
            if r.get(y_key) is not None and r.get(x_key) is not None]
    if len(rows) < (max_lag + 1) * 3:
        log.warning("[granger] 표본 부족(%d) — 스킵", len(rows))
        return {}
    try:
        import numpy as np
        from statsmodels.tsa.stattools import grangercausalitytests
        data = np.array(rows, dtype=float)  # [ [y, x], ... ]
        res = grangercausalitytests(data, maxlag=max_lag, verbose=False)
        return {lag: round(float(res[lag][0]["ssr_ftest"][1]), 4)
                for lag in res}
    except ImportError:
        log.warning("[granger] statsmodels/numpy 미설치 — 스킵")
        return {}
    except Exception as e:  # 특이행렬 등 방어
        log.error("[granger] 실패: %s", e)
        return {}


@dataclass
class EventStudyResult:
    event_name: str
    n_events: int = 0
    horizons: dict[int, dict[str, float]] = field(default_factory=dict)
    baseline: dict[int, dict[str, float]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"event_name": self.event_name, "n_events": self.n_events,
                "horizons": self.horizons, "baseline": self.baseline}


def _fwd_return(series: list[dict], i: int, h: int) -> float | None:
    j = i + h
    if j >= len(series):
        return None
    c0, cj = series[i].get("close"), series[j].get("close")
    if not c0 or cj is None:
        return None
    return (cj - c0) / c0 * 100.0


def _stats(vals: list[float]) -> dict[str, float]:
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "mean": round(statistics.mean(vals), 3),
        "median": round(statistics.median(vals), 3),
        "stdev": round(statistics.pstdev(vals), 3) if len(vals) > 1 else 0.0,
        "win_rate": round(sum(1 for v in vals if v > 0) / len(vals), 3),
    }


def event_study(series: list[dict], event_key: str, event_name: str,
                horizons: tuple[int, ...] = (5, 20)) -> EventStudyResult:
    """이벤트 이후 +h일 수익률 분포 vs 비이벤트 기준선.

    event_key: series 각 행에서 True/1 이면 그 날을 이벤트로 간주.
    """
    res = EventStudyResult(event_name=event_name)
    event_idx = [i for i, r in enumerate(series) if r.get(event_key)]
    base_idx = [i for i, r in enumerate(series) if not r.get(event_key)]
    res.n_events = len(event_idx)
    for h in horizons:
        res.horizons[h] = _stats([_fwd_return(series, i, h) for i in event_idx])
        res.baseline[h] = _stats([_fwd_return(series, i, h) for i in base_idx])
    return res


def mark_events(series: list[dict], *, nsi_change_window: int = 5,
                nsi_change_threshold: float = 30.0) -> list[dict]:
    """이벤트 플래그 부여: 급변점(NSI 5일 변화폭≥30) / 극단 쏠림(extreme_flag).

    원본을 복사하지 않고 각 행에 event_* 키를 추가한다.
    """
    for i, r in enumerate(series):
        j = i - nsi_change_window
        swing = None
        if j >= 0 and r["nsi_wt"] is not None and series[j]["nsi_wt"] is not None:
            swing = abs(r["nsi_wt"] - series[j]["nsi_wt"])
        r["event_swing"] = 1 if (swing is not None and swing >= nsi_change_threshold) else 0
        r["event_extreme"] = 1 if r.get("extreme") else 0
    return series


def export_features(db, theme: str, symbol: str, path: str) -> str | None:
    """WFO 피처 export: 일별 NSI/변화율/괴리/극단 → parquet(불가 시 csv).

    반드시 아웃오브샘플 성과로 판정할 것 — 인샘플만 개선되면 해당 피처 폐기.
    """
    series = build_joined_series(db, theme, symbol)
    if not series:
        log.warning("[export] 조인 데이터 없음")
        return None
    cols = ["date", "nsi_wt", "nsi_change", "divergence", "extreme", "ret"]
    records = [{k: r.get(k) for k in cols} for r in series]
    try:
        import pandas as pd
        df = pd.DataFrame(records)
        try:
            df.to_parquet(path, index=False)
            return path
        except Exception:  # pyarrow 없으면 csv
            csv_path = path.rsplit(".", 1)[0] + ".csv"
            df.to_csv(csv_path, index=False)
            log.warning("[export] parquet 불가 → csv 로 저장: %s", csv_path)
            return csv_path
    except ImportError:
        import csv
        csv_path = path.rsplit(".", 1)[0] + ".csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(records)
        return csv_path
