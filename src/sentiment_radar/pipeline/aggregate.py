"""집계 엔진 — LLM 없이 순수 Python.

분류된 아이템을 일별(KST) 버킷으로 모아 Raw / 가중 비율과 NSI 를 계산한다.

가중치: weight = source_weight × reach_factor × confidence
  - reach_factor = 1 + log(1 + reach_score)
    (스펙의 log(1+reach) 를 그대로 쓰면 reach=0 인 뉴스가 가중치 0 이 되어
     사실상 배제되므로, 하한 1 을 두어 reach 는 '가산 부스트'로만 작동시킨다.)
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..config import settings
from ..models import SENTIMENTS

log = logging.getLogger(__name__)


@dataclass
class ScopeMetrics:
    n_items: int = 0
    pct_pos_raw: float = 0.0
    pct_neu_raw: float = 0.0
    pct_neg_raw: float = 0.0
    nsi_raw: float = 0.0
    pct_pos_wt: float = 0.0
    pct_neu_wt: float = 0.0
    pct_neg_wt: float = 0.0
    nsi_wt: float = 0.0
    extreme_flag: int = 0
    divergence: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_items": self.n_items,
            "pct_pos_raw": self.pct_pos_raw,
            "pct_neu_raw": self.pct_neu_raw,
            "pct_neg_raw": self.pct_neg_raw,
            "nsi_raw": self.nsi_raw,
            "pct_pos_wt": self.pct_pos_wt,
            "pct_neu_wt": self.pct_neu_wt,
            "pct_neg_wt": self.pct_neg_wt,
            "nsi_wt": self.nsi_wt,
            "extreme_flag": self.extreme_flag,
            "divergence": self.divergence,
        }


def reach_factor(reach_score: float) -> float:
    return 1.0 + math.log1p(max(0.0, float(reach_score or 0.0)))


def _compute_scope(
    records: list[dict[str, Any]],
    source_weights: dict[str, float],
    extreme_threshold: float,
) -> ScopeMetrics:
    """단일 스코프(레코드 부분집합)에 대한 지표 계산."""
    m = ScopeMetrics(n_items=len(records))
    if not records:
        return m

    raw_counts = {s: 0 for s in SENTIMENTS}
    wt_sums = {s: 0.0 for s in SENTIMENTS}
    total_w = 0.0

    for r in records:
        s = r["sentiment"]
        if s not in SENTIMENTS:
            continue
        raw_counts[s] += 1
        sw = float(source_weights.get(r.get("source_type", ""), 1.0))
        conf = float(r.get("confidence", 0.5))
        w = sw * reach_factor(r.get("reach_score", 0.0)) * conf
        wt_sums[s] += w
        total_w += w

    n = len(records)
    m.pct_pos_raw = 100.0 * raw_counts["positive"] / n
    m.pct_neu_raw = 100.0 * raw_counts["neutral"] / n
    m.pct_neg_raw = 100.0 * raw_counts["negative"] / n
    m.nsi_raw = m.pct_pos_raw - m.pct_neg_raw

    if total_w > 0:
        m.pct_pos_wt = 100.0 * wt_sums["positive"] / total_w
        m.pct_neu_wt = 100.0 * wt_sums["neutral"] / total_w
        m.pct_neg_wt = 100.0 * wt_sums["negative"] / total_w
        m.nsi_wt = m.pct_pos_wt - m.pct_neg_wt

    # 쏠림 경보: raw 또는 가중 어느 쪽이든 한 방향이 임계 이상
    thr = extreme_threshold * 100.0
    if (
        m.pct_pos_raw >= thr or m.pct_neg_raw >= thr
        or m.pct_pos_wt >= thr or m.pct_neg_wt >= thr
    ):
        m.extreme_flag = 1
    return m


def aggregate_records(
    records: Iterable[dict[str, Any]],
    cfg: dict[str, Any] | None = None,
) -> dict[str, ScopeMetrics]:
    """레코드들을 스코프별로 집계.

    records 각 항목 키: source_type, sentiment, confidence, reach_score, is_opinion
    반환: {scope: ScopeMetrics}  (scope = all|institutional|retail|<source_type>)
    'all' 스코프에는 기관-리테일 divergence 를 채운다.
    """
    cfg = cfg or settings()
    agg_cfg = cfg.get("aggregation", {})
    source_weights = cfg.get("source_weights", {})
    groups = cfg.get("source_groups", {})
    exclude_non_opinion = bool(agg_cfg.get("exclude_non_opinion", True))
    min_conf = float(agg_cfg.get("min_confidence", 0.0))
    extreme_threshold = float(agg_cfg.get("extreme_threshold", 0.75))

    # 필터링 (사실보도 제외 + 저신뢰 제외)
    filtered = []
    for r in records:
        if exclude_non_opinion and not _is_opinion(r):
            continue
        if float(r.get("confidence", 0.0)) < min_conf:
            continue
        filtered.append(r)

    result: dict[str, ScopeMetrics] = {}
    result["all"] = _compute_scope(filtered, source_weights, extreme_threshold)

    # 소스 그룹 스코프
    inst_types = set(groups.get("institutional", []))
    retail_types = set(groups.get("retail", []))
    inst = [r for r in filtered if r.get("source_type") in inst_types]
    retail = [r for r in filtered if r.get("source_type") in retail_types]
    result["institutional"] = _compute_scope(inst, source_weights, extreme_threshold)
    result["retail"] = _compute_scope(retail, source_weights, extreme_threshold)

    # 소스 타입별 스코프
    by_type: dict[str, list] = defaultdict(list)
    for r in filtered:
        by_type[r.get("source_type", "unknown")].append(r)
    for stype, recs in by_type.items():
        result[stype] = _compute_scope(recs, source_weights, extreme_threshold)

    # 괴리 (기관 NSI - 리테일 NSI), 양쪽 모두 데이터 있을 때만
    if inst and retail:
        result["all"].divergence = (
            result["institutional"].nsi_wt - result["retail"].nsi_wt
        )
    return result


def _is_opinion(r: dict[str, Any]) -> bool:
    v = r.get("is_opinion", True)
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in {"true", "1", "yes", "y"}
    return bool(v)


def compute_and_store(db, theme: str, cfg: dict[str, Any] | None = None) -> dict[str, int]:
    """분류된 아이템을 KST 일별로 집계해 daily_aggregates 에 저장.

    반환: {bucket_date: 저장된 스코프 수}
    """
    from ..models import to_kst_date, utcnow_iso

    rows = db.fetch_classified(theme)
    # KST 날짜별 버킷 (published_at 우선, 없으면 collected_at)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        d = to_kst_date(r["published_at"]) or to_kst_date(r["collected_at"])
        if not d:
            continue
        buckets[d].append(
            {
                "source_type": r["source_type"],
                "sentiment": r["sentiment"],
                "confidence": r["confidence"],
                "reach_score": r["reach_score"],
                "is_opinion": r["is_opinion"],
            }
        )

    now = utcnow_iso()
    summary: dict[str, int] = {}
    for date, recs in buckets.items():
        scopes = aggregate_records(recs, cfg)
        saved = 0
        for scope, metrics in scopes.items():
            if metrics.n_items == 0:
                continue
            db.upsert_aggregate(theme, date, scope, metrics.as_dict(), now)
            saved += 1
        summary[date] = saved
        log.info("[aggregate] %s: %d개 스코프 저장", date, saved)
    return summary
