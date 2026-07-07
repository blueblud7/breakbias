"""통합 파이프라인 러너 (M5).

수집 → 분류 → 집계 → 예측판정 → 총평 → 규칙알림 을 한 번에 실행한다.
각 단계는 격리되어 하나가 실패해도 나머지는 진행하고, 실패는 Notifier 로 알린다.
스케줄러/CLI/FastAPI 가 공통으로 이 함수를 호출한다.

의존성(classifier/commentary_gen/price_provider/notifier/collector_instances)은
주입 가능 — 테스트 시 가짜 구현으로 대체한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from . import collectors as collectors_mod
from .attention import collect_trends
from .config import load_theme, settings
from .llm.classifier import Classifier
from .llm.commentary import CommentaryGenerator
from .models import to_kst_date, utcnow_iso
from .notify import Notifier
from .pipeline import compute_and_store, dedup_items, run_classification
from .predictions import PredictionJournal
from .rules import RuleEngine

log = logging.getLogger(__name__)


@dataclass
class PipelineReport:
    theme: str
    collected: int = 0
    classified: int = 0
    aggregated_dates: int = 0
    resolved_predictions: int = 0
    commentary: bool = False
    rules_fired: list[int] = field(default_factory=list)
    cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=utcnow_iso)
    finished_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


def run_full_pipeline(
    theme_name: str,
    *,
    db,
    classifier: Classifier | None = None,
    commentary_gen: CommentaryGenerator | None = None,
    price_provider=None,
    notifier: Notifier | None = None,
    collector_instances: list | None = None,
    do_collect: bool = True,
    with_trends: bool = True,
    do_commentary: bool = True,
) -> PipelineReport:
    theme = load_theme(theme_name)
    rep = PipelineReport(theme=theme.theme)
    notifier = notifier or Notifier()

    def stage(name, fn):
        try:
            return fn()
        except Exception as e:  # 단계 격리
            msg = f"[{name}] 실패: {e}"
            log.exception(msg)
            rep.errors.append(msg)
            return None

    # 1) 수집 + dedup
    if do_collect:
        def _collect():
            instances = collector_instances or [
                collectors_mod.REGISTRY[s]() for s in collectors_mod.available()
            ]
            items = []
            for col in instances:
                got = col.collect(theme)
                log.info("  [%s] %d건", col.source_type, len(got))
                items.extend(got)
            if not items:
                return 0
            lookback = int(settings().get("dedup", {}).get("lookback_days", 3))
            existing = db.recent_hashes(theme.theme, lookback)
            return db.insert_items(dedup_items(items, existing_hashes=existing))
        rep.collected = stage("collect", _collect) or 0

        if with_trends and theme.trends_pairs:
            stage("trends", lambda: collect_trends(db, theme))

    # 2) 분류
    def _classify():
        clf = classifier or Classifier()
        res = run_classification(db, theme, clf)
        rep.cost_usd += res.cost_usd
        return res.classified
    rep.classified = stage("classify", _classify) or 0

    # 3) 집계
    def _aggregate():
        return len(compute_and_store(db, theme.theme))
    rep.aggregated_dates = stage("aggregate", _aggregate) or 0

    # 4) 예측 판정
    def _resolve():
        j = PredictionJournal(db, price_provider) if price_provider else PredictionJournal(db)
        return len(j.resolve_due())
    rep.resolved_predictions = stage("resolve", _resolve) or 0

    # 5) 총평
    if do_commentary:
        def _commentary():
            gen = commentary_gen or CommentaryGenerator()
            return gen.generate(db, theme.theme, to_kst_date(utcnow_iso())) is not None
        rep.commentary = bool(stage("commentary", _commentary))

    # 6) 규칙 평가 + 알림
    def _rules():
        eng = RuleEngine(db, notifier)
        return eng.check_and_notify(theme.theme)
    rep.rules_fired = stage("rules", _rules) or []

    rep.finished_at = utcnow_iso()

    # 실패가 있으면 알림
    if rep.errors:
        notifier.send(
            f"⚠️ [{theme.theme}] 파이프라인 {len(rep.errors)}개 단계 실패:\n"
            + "\n".join(rep.errors[:5])
        )
    log.info("[pipeline] 완료: %s", rep.as_dict())
    return rep
