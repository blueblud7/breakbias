"""LLM 비용 추정 + 로깅 + 일 예산 관리.

가격표는 추정치이며 config/settings.yaml 의 llm.pricing 으로 덮어쓸 수 있다.
단위: USD per 1M tokens.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ..config import settings
from ..models import KST

log = logging.getLogger(__name__)

# 기본 가격표 (USD / 1M tokens) — 실제 요금으로 갱신 권장.
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "gpt-5-nano": {"input": 0.05, "output": 0.40},
    "deepseek-v4-pro": {"input": 0.27, "output": 1.10},
}


def _pricing(model: str) -> dict[str, float]:
    override = settings().get("llm", {}).get("pricing", {})
    table = {**DEFAULT_PRICING, **override}
    # 모델명 부분 일치 허용 (예: gpt-5-nano-2025-xx)
    if model in table:
        return table[model]
    for key, val in table.items():
        if model.startswith(key):
            return val
    log.warning("[cost] 모델 '%s' 가격 정보 없음 — 0 으로 계산", model)
    return {"input": 0.0, "output": 0.0}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """토큰 사용량으로 USD 비용 추정."""
    p = _pricing(model)
    return (
        prompt_tokens / 1_000_000 * p["input"]
        + completion_tokens / 1_000_000 * p["output"]
    )


def kst_today() -> str:
    return datetime.now(KST).date().isoformat()


class CostTracker:
    """호출별 비용을 llm_cost_log 에 적재하고 일 예산 초과 여부를 판단."""

    def __init__(self, db, model: str, call_type: str = "classify") -> None:
        self.db = db
        self.model = model
        self.call_type = call_type
        self.budget = float(settings().get("llm", {}).get("daily_budget_usd", 2.0))

    def record(self, prompt_tokens: int, completion_tokens: int, n_calls: int = 1) -> float:
        cost = estimate_cost(self.model, prompt_tokens, completion_tokens)
        self.db.add_cost_log(
            bucket_date=kst_today(),
            model=self.model,
            call_type=self.call_type,
            n_calls=n_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
        )
        return cost

    def today_spent(self) -> float:
        return self.db.today_cost_usd(kst_today())

    def over_budget(self) -> bool:
        if self.budget <= 0:
            return False
        return self.today_spent() >= self.budget
