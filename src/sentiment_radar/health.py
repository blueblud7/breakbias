"""데이터 무결성 / 헬스 체크 (M5).

'3일 무인 운영 후 데이터 무결성 확인' 완료 기준을 자동 점검한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .config import settings
from .models import KST


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class HealthReport:
    theme: str
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append(Check(name, ok, detail))


def _kst_today():
    return datetime.now(KST).date()


def health_report(db, theme: str, *, recent_days: int = 3,
                  max_stale_hours: int = 30) -> HealthReport:
    rep = HealthReport(theme=theme)

    # 1) 최근 수집 시각이 너무 오래되지 않았나
    last = db.last_collected_at(theme)
    if last is None:
        rep.add("수집 이력", False, "수집된 아이템 없음")
    else:
        try:
            dt = datetime.fromisoformat(last)
            from datetime import timezone
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
            ok = age_h <= max_stale_hours
            rep.add("수집 신선도", ok, f"마지막 수집 {age_h:.1f}시간 전")
        except ValueError:
            rep.add("수집 신선도", False, f"시각 파싱 실패: {last}")

    # 2) 최근 N일 각각 집계가 존재하는가
    dates = set(db.aggregate_dates(theme, limit=recent_days + 5))
    missing = []
    for i in range(recent_days):
        d = (_kst_today() - timedelta(days=i)).isoformat()
        if d not in dates:
            missing.append(d)
    rep.add("일별 집계 커버리지", len(missing) == 0,
            "누락 없음" if not missing else f"누락: {', '.join(missing)}")

    # 3) 미분류 적체 (수집됐지만 분류 안 된 것)
    unclassified = db.count_unclassified(theme)
    rep.add("미분류 적체", unclassified < 500, f"미분류 {unclassified}건")

    # 4) 비용이 예산 내인가 (오늘)
    budget = float(settings().get("llm", {}).get("daily_budget_usd", 2.0))
    today_cost = db.today_cost_usd(_kst_today().isoformat())
    rep.add("일 예산", budget <= 0 or today_cost <= budget,
            f"오늘 ${today_cost:.4f} / 예산 ${budget:.2f}")

    return rep


def cost_summary(db, days: int = 30) -> dict:
    """비용 대시보드용 요약."""
    rows = db.cost_by_date(limit=days)
    by_date = [
        {"bucket_date": r["bucket_date"], "cost_usd": round(r["cost_usd"] or 0, 5),
         "n_calls": r["n_calls"] or 0,
         "prompt_tokens": r["prompt_tokens"] or 0,
         "completion_tokens": r["completion_tokens"] or 0}
        for r in rows
    ]
    by_model = [{"model": r["model"], "cost_usd": round(r["cost_usd"] or 0, 5),
                 "n_calls": r["n_calls"] or 0} for r in db.cost_by_model()]
    total = round(sum(d["cost_usd"] for d in by_date), 5)
    budget = float(settings().get("llm", {}).get("daily_budget_usd", 2.0))
    today = _kst_today().isoformat()
    today_cost = next((d["cost_usd"] for d in by_date if d["bucket_date"] == today), 0.0)
    return {
        "by_date": list(reversed(by_date)),   # 오름차순
        "by_model": by_model,
        "total_usd": total,
        "budget_usd": budget,
        "today_usd": today_cost,
        "over_budget": budget > 0 and today_cost >= budget,
    }
