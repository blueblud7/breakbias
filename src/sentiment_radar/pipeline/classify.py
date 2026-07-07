"""분류 파이프라인 오케스트레이션.

미분류 아이템을 배치로 gpt-5-nano 에 보내 분류·저장하고 비용을 기록한다.
일 예산 초과 시 분류를 중단(수집물은 다음날 처리).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..config import Theme
from ..llm.classifier import Classifier
from ..llm.cost import CostTracker
from ..models import Classification

log = logging.getLogger(__name__)


@dataclass
class ClassifyResult:
    total: int = 0
    classified: int = 0
    failed: int = 0
    skipped_budget: int = 0
    cost_usd: float = 0.0


def run_classification(
    db,
    theme: Theme,
    classifier: Classifier,
    *,
    limit: int = 500,
) -> ClassifyResult:
    """미분류 아이템을 분류해 저장. ClassifyResult 반환."""
    res = ClassifyResult()
    if not classifier.enabled:
        log.warning("[classify] 분류기 비활성(키/패키지 없음) — 스킵")
        return res

    tracker = CostTracker(db, classifier.model, call_type="classify")
    if tracker.over_budget():
        log.warning("[classify] 일 예산 초과 상태 — 분류 스킵")
        return res

    rows = db.fetch_unclassified(theme.theme, limit=limit)
    res.total = len(rows)
    log.info("[classify] 미분류 %d건 처리 시작 (model=%s)", res.total, classifier.model)

    for row in rows:
        if tracker.over_budget():
            remaining = res.total - res.classified - res.failed
            res.skipped_budget = remaining
            log.warning("[classify] 예산 초과 — 남은 %d건 다음날로 미룸", remaining)
            break

        parsed, pt, ct = classifier.classify(
            theme_name=theme.display_name,
            title=row["title"],
            snippet=row["content_snippet"] or "",
            source_type=row["source_type"],
        )
        res.cost_usd += tracker.record(pt, ct, n_calls=1)

        if parsed is None:
            res.failed += 1
            continue

        try:
            clf = Classification(
                item_id=row["id"], model=classifier.model, **parsed
            )
        except (ValueError, TypeError) as e:
            log.error("[classify] item %s 분류결과 검증 실패: %s", row["id"], e)
            res.failed += 1
            continue

        db.insert_classification(clf.to_row())
        res.classified += 1

    log.info(
        "[classify] 완료: 분류=%d 실패=%d 예산스킵=%d 비용=$%.4f",
        res.classified, res.failed, res.skipped_budget, res.cost_usd,
    )
    return res
