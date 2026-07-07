#!/usr/bin/env python3
"""수집 CLI 엔트리포인트.

사용:
    python collect.py --theme 반도체
    python collect.py --theme semiconductor --sources news_kr,news_global
    python collect.py --list-themes

완료 기준(M1): items 테이블에 수집 결과가 저장된다.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# src 레이아웃을 sys.path 에 추가 (설치 없이 실행 가능하도록)
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from sentiment_radar import collectors  # noqa: E402
from sentiment_radar.attention import collect_trends  # noqa: E402
from sentiment_radar.config import list_themes, load_theme, settings  # noqa: E402
from sentiment_radar.db import get_db  # noqa: E402
from sentiment_radar.pipeline import dedup_items  # noqa: E402


def setup_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def run(theme_name: str, sources: list[str] | None, *, with_trends: bool = True) -> int:
    log = logging.getLogger("collect")
    theme = load_theme(theme_name)
    log.info("테마 '%s' (%s) 수집 시작", theme.display_name, theme.theme)

    selected = sources or collectors.available()
    all_items = []
    for stype in selected:
        cls = collectors.REGISTRY.get(stype)
        if cls is None:
            log.warning("알 수 없는 소스 '%s' — 건너뜀 (사용가능: %s)",
                        stype, collectors.available())
            continue
        collector = cls()
        got = collector.collect(theme)
        log.info("  [%s] %d건 수집", stype, len(got))
        all_items.extend(got)

    lookback = int(settings().get("dedup", {}).get("lookback_days", 3))
    inserted = 0
    with get_db() as db:
        if all_items:
            existing = db.recent_hashes(theme.theme, lookback)
            deduped = dedup_items(all_items, existing_hashes=existing)
            inserted = db.insert_items(deduped)
            total = db.count_items(theme.theme)
            log.info("저장 완료: 신규 %d건 (테마 누적 %d건)", inserted, total)
        else:
            log.warning("수집된 아이템이 없습니다. (API 키 설정 여부 확인)")

        # Google Trends 관심도 (attention 별도 트랙)
        if with_trends and theme.trends_pairs:
            n = collect_trends(db, theme)
            log.info("  [trends] 관심도 지표 %d건 저장", n)

    return inserted


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Market Sentiment Radar 수집기")
    parser.add_argument("--theme", help="수집할 테마 (예: 반도체 / semiconductor)")
    parser.add_argument(
        "--sources",
        help="쉼표구분 소스 목록 (기본: 전체). 예: news_kr,news_global",
    )
    parser.add_argument("--list-themes", action="store_true", help="테마 목록 출력")
    parser.add_argument("--no-trends", action="store_true", help="Google Trends 수집 생략")
    args = parser.parse_args()

    if args.list_themes:
        print("등록된 테마:", ", ".join(list_themes()) or "(없음)")
        print("사용가능 소스:", ", ".join(collectors.available()))
        return
    if not args.theme:
        parser.error("--theme 를 지정하세요 (또는 --list-themes).")

    sources = [s.strip() for s in args.sources.split(",")] if args.sources else None
    run(args.theme, sources, with_trends=not args.no_trends)


if __name__ == "__main__":
    main()
