#!/usr/bin/env python3
"""자동 수집 스케줄러 (M5) — APScheduler.

하루 2회: 한국장 마감 후(16:00 KST), 미국장 마감 후(06:30 KST).
각 실행은 전 테마에 대해 run_full_pipeline 을 돌리고, 실패 시 텔레그램으로 알린다.

사용: python scheduler.py            # 블로킹 실행 (Ctrl+C 종료)
      python scheduler.py --once     # 즉시 1회 실행 후 종료 (테스트/수동)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from sentiment_radar.config import list_themes  # noqa: E402
from sentiment_radar.db import get_db  # noqa: E402
from sentiment_radar.notify import Notifier  # noqa: E402
from sentiment_radar.orchestrate import run_full_pipeline  # noqa: E402

log = logging.getLogger("scheduler")


def run_all_themes(tag: str = "") -> None:
    themes = list_themes() or ["semiconductor"]
    notifier = Notifier()
    log.info("[scheduler] %s 실행 시작 (테마 %d개)", tag, len(themes))
    with get_db() as db:
        for theme in themes:
            try:
                rep = run_full_pipeline(theme, db=db, notifier=notifier)
                log.info("[scheduler] %s: 수집 %d 분류 %d 집계 %d일 규칙 %d건",
                         theme, rep.collected, rep.classified,
                         rep.aggregated_dates, len(rep.rules_fired))
            except Exception as e:  # 전역 방어
                log.exception("[scheduler] %s 실패", theme)
                notifier.send(f"⚠️ 스케줄러 {theme} 실행 실패: {e}")


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="즉시 1회 실행 후 종료")
    args = ap.parse_args()

    if args.once:
        run_all_themes("수동")
        return

    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched = BlockingScheduler(timezone="Asia/Seoul")
    sched.add_job(lambda: run_all_themes("한국장마감후"),
                  CronTrigger(hour=16, minute=0), id="kr_close")
    sched.add_job(lambda: run_all_themes("미국장마감후"),
                  CronTrigger(hour=6, minute=30), id="us_close")
    log.info("[scheduler] 시작 — 16:00 / 06:30 KST 하루 2회. Ctrl+C 로 종료.")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("[scheduler] 종료")


if __name__ == "__main__":
    main()
