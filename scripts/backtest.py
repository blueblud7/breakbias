#!/usr/bin/env python3
"""센티먼트-수익률 백테스트 리포트 (M7).

사용:
    python scripts/backtest.py --theme semiconductor --symbol 1001
    python scripts/backtest.py --theme semiconductor --symbol 1001 --export features.parquet
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sentiment_radar.backtest import (  # noqa: E402
    INTERPRETATION_GUIDE, build_joined_series, event_study, export_features,
    granger_pvalues, lag_correlation, mark_events,
)
from sentiment_radar.config import load_theme  # noqa: E402
from sentiment_radar.db import get_db  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", required=True)
    ap.add_argument("--symbol", default=None, help="지수코드 (기본: 테마 kospi)")
    ap.add_argument("--max-lag", type=int, default=10)
    ap.add_argument("--export", default=None, help="WFO 피처 파일 경로(.parquet)")
    args = ap.parse_args()

    theme = load_theme(args.theme)
    symbol = args.symbol or theme.price_symbols.get("kospi", "1001")

    with get_db() as db:
        series = build_joined_series(db, theme.theme, symbol)
        print(f"\n=== 백테스트: {theme.display_name} vs {symbol} ===")
        print(f"조인된 거래일: {len(series)}일")
        if len(series) < 10:
            print("데이터가 부족합니다. 백필(scripts/backfill.py) 또는 seed_demo 후 재시도.")
            print(f"\n[해석 가이드] {INTERPRETATION_GUIDE}")
            return

        # 1) 시차 상관
        print("\n[1] 시차 상관 (NSI → 수익률)  * lag>0 = 센티먼트 선행")
        lc = lag_correlation(series, "nsi_wt", "ret", args.max_lag)
        for k in sorted(lc):
            v = lc[k]
            bar = ""
            if v is not None:
                bar = ("+" if v >= 0 else "-") * int(abs(v) * 40)
            print(f"  lag {k:+3d}: {v if v is not None else 'NA':>7} {bar}")

        lc_chg = lag_correlation(series, "nsi_change", "ret", args.max_lag)
        best = max((k for k in lc_chg if lc_chg[k] is not None),
                   key=lambda k: abs(lc_chg[k]), default=None)
        if best is not None:
            print(f"  NSI 변화율: 최대 상관 lag {best:+d} ({lc_chg[best]})")

        # Granger
        gp = granger_pvalues(series, "nsi_change", "ret", max_lag=min(5, args.max_lag))
        if gp:
            print("\n  Granger 인과 (NSI변화→수익률) p-value  * p<0.05 = 선행 시사")
            for lag, p in gp.items():
                sig = " ★" if p < 0.05 else ""
                print(f"    lag {lag}: p={p}{sig}")
        else:
            print("\n  Granger: 표본 부족 또는 statsmodels 미설치 — 스킵")

        # 2) 이벤트 스터디
        mark_events(series)
        print("\n[2] 이벤트 스터디 (이벤트 후 수익률 vs 기준선)")
        for key, name in [("event_swing", "NSI 5일 급변(≥30pt)"),
                          ("event_extreme", "극단 쏠림(≥75%)")]:
            es = event_study(series, key, name)
            print(f"  · {name}: 이벤트 {es.n_events}회")
            for h in es.horizons:
                e, b = es.horizons[h], es.baseline[h]
                if e.get("n"):
                    print(f"      +{h}일: 이벤트 평균 {e.get('mean')}% (승률 {e.get('win_rate')}) "
                          f"vs 기준선 {b.get('mean')}%")

        # 3) WFO 피처 export
        if args.export:
            out = export_features(db, theme.theme, symbol, args.export)
            print(f"\n[3] WFO 피처 export: {out}")
            print("    ※ 반드시 아웃오브샘플 성과로 판정. 인샘플만 개선되면 폐기.")

        print(f"\n[해석 가이드] {INTERPRETATION_GUIDE}")


if __name__ == "__main__":
    main()
