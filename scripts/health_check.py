#!/usr/bin/env python3
"""데이터 무결성 체크 CLI (M5).

3일 무인 운영 후 데이터 무결성 확인용. 문제가 있으면 종료코드 1.

사용: python scripts/health_check.py [--theme semiconductor]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sentiment_radar.config import list_themes  # noqa: E402
from sentiment_radar.db import get_db  # noqa: E402
from sentiment_radar.health import health_report  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", default=None, help="미지정 시 전체 테마")
    args = ap.parse_args()

    themes = [args.theme] if args.theme else (list_themes() or ["semiconductor"])
    all_ok = True
    with get_db() as db:
        for theme in themes:
            rep = health_report(db, theme)
            status = "✅ OK" if rep.ok else "❌ 문제"
            print(f"\n=== {theme}: {status} ===")
            for c in rep.checks:
                mark = "✓" if c.ok else "✗"
                print(f"  [{mark}] {c.name}: {c.detail}")
            all_ok = all_ok and rep.ok
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
