#!/usr/bin/env python3
"""예측 일지 + 사전 규칙 CLI (M6 — 확증편향 교정 코어).

예측:
    python journal.py predict --theme 반도체 --view positive --confidence 70 \
        --horizon 14 --symbol 1001 --basis "HBM 수요 지속"
    python journal.py resolve --theme 반도체          # 만기 예측 판정
    python journal.py status  --theme 반도체          # Brier + 캘리브레이션

규칙:
    python journal.py rule-add --name "극단 약세" --metric nsi_wt --op "<=" \
        --value -60 --consecutive 3 --action "역발상 관점 검토" --theme 반도체
    python journal.py rule-list
    python journal.py rule-check --theme 반도체        # 조건 충족 시 알림
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from sentiment_radar.db import get_db  # noqa: E402
from sentiment_radar.predictions import PredictionJournal  # noqa: E402
from sentiment_radar.rules import RuleEngine  # noqa: E402


def setup_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_predict(args, db):
    j = PredictionJournal(db)
    pid = j.add(theme=args.theme, my_view=args.view, confidence=args.confidence,
                horizon_days=args.horizon, basis=args.basis, target_symbol=args.symbol)
    print(f"예측 #{pid} 기록 완료.")


def cmd_resolve(args, db):
    j = PredictionJournal(db)
    resolved = j.resolve_due()
    print(f"판정 완료: {len(resolved)}건")
    for r in resolved:
        print(f"  #{r['id']}: {r['outcome']} (수익률 {r['actual_return']:.2f}%, "
              f"Brier {r['brier']:.3f})")


def cmd_status(args, db):
    j = PredictionJournal(db)
    s = j.brier_summary(args.theme)
    print(f"\n=== 예측 성과 ({args.theme or '전체'}) ===")
    if s.n == 0:
        print("아직 판정된 예측이 없습니다.")
        return
    print(f"판정 {s.n}건 | 적중률 {s.hit_rate*100:.0f}% | Brier {s.brier_mean:.3f}")
    if s.recent_hit_rate is not None:
        print(f"최근 {s.recent_n}회 적중률 {s.recent_hit_rate*100:.0f}%")
    print("\n캘리브레이션 (확신도구간: 평균확신 vs 실제적중):")
    for c in j.calibration(args.theme):
        if c["n"] == 0:
            continue
        gap = c["avg_confidence"] - c["actual_hit_rate"]
        flag = "  ← 과신" if gap > 0.1 else ("  ← 과소" if gap < -0.1 else "")
        print(f"  {c['bin']:>7}%: n={c['n']:>2} "
              f"확신 {c['avg_confidence']*100:.0f}% / 적중 {c['actual_hit_rate']*100:.0f}%{flag}")


def cmd_rule_add(args, db):
    eng = RuleEngine(db)
    cond = {"metric": args.metric, "scope": args.scope, "op": args.op,
            "value": args.value, "consecutive_days": args.consecutive}
    rid = eng.add_rule(name=args.name, condition=cond, action_text=args.action,
                       theme=args.theme)
    print(f"규칙 #{rid} 등록 완료.")


def cmd_rule_list(args, db):
    rules = db.fetch_rules(active_only=False)
    if not rules:
        print("등록된 규칙이 없습니다.")
        return
    for r in rules:
        state = "활성" if r["active"] else "비활성"
        print(f"#{r['id']} [{state}] {r['name']} — {r['condition_json']}")
        print(f"      권고: {r['action_text'] or '(없음)'} | 등록 {r['created_at'][:10]}"
              f" | 최근발동 {r['last_triggered_at'] or '-'}")
        hist = db.fetch_rule_history(r["id"])
        if len(hist) > 1:
            print(f"      수정이력 {len(hist)}건: "
                  + ", ".join(f"{h['change_type']}@{h['changed_at'][:10]}" for h in hist))


def cmd_rule_check(args, db):
    eng = RuleEngine(db)
    fired = eng.check_and_notify(args.theme)
    print(f"발동한 규칙: {len(fired)}건 {fired if fired else ''}")


def main() -> None:
    setup_logging()
    ap = argparse.ArgumentParser(description="예측 일지 + 사전 규칙 (M6)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("predict", help="예측 기록")
    p.add_argument("--theme", required=True)
    p.add_argument("--view", required=True, choices=["positive", "negative"])
    p.add_argument("--confidence", required=True, type=float, help="50~100")
    p.add_argument("--horizon", required=True, type=int, help="예측 기간(일)")
    p.add_argument("--symbol", required=True, help="지수코드 (1001=코스피, ^SOX 등)")
    p.add_argument("--basis", default="", help="근거 한 줄")
    p.set_defaults(func=cmd_predict)

    p = sub.add_parser("resolve", help="만기 예측 판정")
    p.add_argument("--theme", default=None)
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("status", help="Brier + 캘리브레이션")
    p.add_argument("--theme", default=None)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("rule-add", help="규칙 등록")
    p.add_argument("--name", required=True)
    p.add_argument("--metric", required=True)
    p.add_argument("--scope", default="all")
    p.add_argument("--op", required=True, choices=["<=", ">=", "<", ">", "==", "!="])
    p.add_argument("--value", required=True, type=float)
    p.add_argument("--consecutive", type=int, default=1)
    p.add_argument("--action", default="")
    p.add_argument("--theme", default=None)
    p.set_defaults(func=cmd_rule_add)

    p = sub.add_parser("rule-list", help="규칙 목록 + 수정이력")
    p.set_defaults(func=cmd_rule_list)

    p = sub.add_parser("rule-check", help="규칙 평가 + 알림")
    p.add_argument("--theme", required=True)
    p.set_defaults(func=cmd_rule_check)

    args = ap.parse_args()
    with get_db() as db:
        args.func(args, db)


if __name__ == "__main__":
    main()
