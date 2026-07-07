#!/usr/bin/env python3
"""분류 + 집계 CLI (M2).

사용:
    python analyze.py --theme 반도체              # 미분류 분류 후 집계
    python analyze.py --theme 반도체 --aggregate-only  # 집계만 (분류 스킵)
    python analyze.py --theme 반도체 --show           # 최근 집계 출력

완료 기준(M2): 일별 aggregate 생성.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from sentiment_radar.config import load_theme  # noqa: E402
from sentiment_radar.db import get_db  # noqa: E402
from sentiment_radar.llm.classifier import Classifier  # noqa: E402
from sentiment_radar.llm.commentary import CommentaryGenerator  # noqa: E402
from sentiment_radar.models import to_kst_date, utcnow_iso  # noqa: E402
from sentiment_radar.pipeline import compute_and_store, run_classification  # noqa: E402


def setup_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    setup_logging()
    log = logging.getLogger("analyze")
    ap = argparse.ArgumentParser(description="센티먼트 분류 + 집계")
    ap.add_argument("--theme", required=True)
    ap.add_argument("--limit", type=int, default=500, help="분류 배치 최대 건수")
    ap.add_argument("--aggregate-only", action="store_true", help="분류 건너뛰고 집계만")
    ap.add_argument("--commentary", action="store_true", help="deepseek 일별 총평 생성")
    ap.add_argument("--show", action="store_true", help="최근 집계(all 스코프) 출력")
    args = ap.parse_args()

    theme = load_theme(args.theme)

    with get_db() as db:
        if not args.aggregate_only:
            clf = Classifier()
            run_classification(db, theme, clf, limit=args.limit)

        summary = compute_and_store(db, theme.theme)
        log.info("집계 완료: %d개 날짜 버킷", len(summary))

        if args.commentary:
            gen = CommentaryGenerator()
            res = gen.generate(db, theme.theme, to_kst_date(utcnow_iso()))
            log.info("총평 생성: %s", "완료" if res else "스킵(키/집계 없음)")

        if args.show:
            print(f"\n=== {theme.display_name} 최근 집계 (scope=all) ===")
            print(f"{'날짜':<12}{'N':>5}{'NSI_raw':>9}{'NSI_wt':>9}"
                  f"{'괴리':>8}{'쏠림':>6}")
            for r in db.fetch_aggregates(theme.theme, "all", limit=14):
                div = f"{r['divergence']:.1f}" if r["divergence"] is not None else "-"
                flag = "⚠" if r["extreme_flag"] else ""
                print(f"{r['bucket_date']:<12}{r['n_items']:>5}"
                      f"{r['nsi_raw']:>9.1f}{r['nsi_wt']:>9.1f}{div:>8}{flag:>6}")


if __name__ == "__main__":
    main()
