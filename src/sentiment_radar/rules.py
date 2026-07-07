"""사전 규칙 엔진 (M6).

상황 발생 '전에' 등록한 규칙을 daily_aggregates 에 대해 평가한다.
- 규칙 수정 시 이력(rule_history)에 스냅샷을 남겨 사후 변경을 스스로 볼 수 있게 함
- 조건 충족 시 알림 발송 (등록 당시 원문 + 등록일 포함)

조건 DSL (condition_json):
{
  "metric": "nsi_wt",          # daily_aggregates 컬럼명
  "scope": "all",              # all|institutional|retail|<source_type>
  "op": "<=",                  # <= | >= | < | > | == | !=
  "value": -60,
  "consecutive_days": 3         # (선택) 최근 N일 연속 충족. 기본 1
}
예) 가중 NSI -60 이하 3일 지속, 기관-리테일 괴리(divergence) 30 이상 등.
"""

from __future__ import annotations

import json
import logging
import operator
from datetime import datetime

from .models import KST, utcnow_iso
from .notify import Notifier

log = logging.getLogger(__name__)

_OPS = {
    "<=": operator.le, ">=": operator.ge, "<": operator.lt,
    ">": operator.gt, "==": operator.eq, "!=": operator.ne,
}

# daily_aggregates 에서 조건 metric 으로 허용할 컬럼
_ALLOWED_METRICS = {
    "nsi_raw", "nsi_wt", "pct_pos_raw", "pct_neg_raw", "pct_neu_raw",
    "pct_pos_wt", "pct_neg_wt", "pct_neu_wt", "divergence", "extreme_flag",
    "n_items",
}


def _kst_now_date() -> str:
    return datetime.now(KST).date().isoformat()


class RuleEngine:
    def __init__(self, db, notifier: Notifier | None = None) -> None:
        self.db = db
        self.notifier = notifier or Notifier()

    # --- 등록/수정 (이력 동반) ---
    def add_rule(self, *, name: str, condition: dict, action_text: str = "",
                 theme: str | None = None) -> int:
        self._validate(condition)
        now = utcnow_iso()
        rule_id = self.db.insert_rule({
            "theme": theme, "name": name,
            "condition_json": json.dumps(condition, ensure_ascii=False),
            "action_text": action_text, "created_at": now, "updated_at": now,
        })
        self._snapshot(rule_id, "create")
        log.info("[rules] #%d 등록: %s", rule_id, name)
        return rule_id

    def update_rule(self, rule_id: int, *, name: str | None = None,
                    condition: dict | None = None,
                    action_text: str | None = None) -> None:
        if condition is not None:
            self._validate(condition)
        self.db.update_rule(
            rule_id,
            name=name,
            condition_json=json.dumps(condition, ensure_ascii=False) if condition else None,
            action_text=action_text,
            updated_at=utcnow_iso(),
        )
        self._snapshot(rule_id, "update")
        log.info("[rules] #%d 수정 (이력 기록됨)", rule_id)

    def deactivate_rule(self, rule_id: int) -> None:
        self.db.set_rule_active(rule_id, False, utcnow_iso())
        self._snapshot(rule_id, "deactivate")

    def _snapshot(self, rule_id: int, change_type: str) -> None:
        rule = self.db.fetch_rule(rule_id)
        snap = {k: rule[k] for k in rule.keys()}
        self.db.insert_rule_history(
            rule_id, change_type, json.dumps(snap, ensure_ascii=False), utcnow_iso()
        )

    @staticmethod
    def _validate(condition: dict) -> None:
        if condition.get("metric") not in _ALLOWED_METRICS:
            raise ValueError(f"허용되지 않은 metric: {condition.get('metric')!r}")
        if condition.get("op") not in _OPS:
            raise ValueError(f"허용되지 않은 op: {condition.get('op')!r}")
        if "value" not in condition:
            raise ValueError("condition 에 value 필요")

    # --- 평가 ---
    def evaluate(self, rule_row, theme: str, as_of: str | None = None) -> bool:
        """규칙 조건이 (as_of 기준) 충족되는지."""
        cond = json.loads(rule_row["condition_json"])
        metric = cond["metric"]
        scope = cond.get("scope", "all")
        op = _OPS[cond["op"]]
        value = cond["value"]
        consecutive = int(cond.get("consecutive_days", 1))

        rows = self.db.fetch_aggregates_asc(theme, scope, limit=consecutive)
        # as_of 지정 시 그 이하 날짜만
        if as_of:
            rows = [r for r in rows if r["bucket_date"] <= as_of]
            rows = rows[-consecutive:]
        if len(rows) < consecutive:
            return False
        for r in rows:
            v = r[metric]
            if v is None or not op(v, value):
                return False
        return True

    def check_and_notify(self, theme: str, as_of: str | None = None) -> list[int]:
        """활성 규칙 평가 → 충족 시 알림. 발동한 rule_id 목록 반환."""
        as_of = as_of or _kst_now_date()
        fired = []
        for rule in self.db.fetch_rules(active_only=True):
            # theme 필터 (rule.theme 이 None 이면 전체 공통)
            if rule["theme"] and rule["theme"] != theme:
                continue
            if self.evaluate(rule, theme, as_of):
                self._fire(rule, theme, as_of)
                fired.append(rule["id"])
        return fired

    def _fire(self, rule, theme: str, as_of: str) -> None:
        created = (rule["created_at"] or "")[:10]
        cond = rule["condition_json"]
        msg = (
            f"⚠️ 규칙 발동: {rule['name']}\n"
            f"테마: {theme} / 기준일: {as_of}\n"
            f"조건: {cond}\n"
            f"권고: {rule['action_text'] or '(없음)'}\n"
            f"— 당신이 {created} 에 정한 규칙입니다."
        )
        self.notifier.send(msg)
        self.db.set_rule_triggered(rule["id"], utcnow_iso())
        log.info("[rules] #%d 발동 알림 전송", rule["id"])
