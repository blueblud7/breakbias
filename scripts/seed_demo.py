#!/usr/bin/env python3
"""데모용 합성 30일 데이터 시드 (API 키 없이 대시보드를 의미있게 렌더).

실제 수집/분류 데이터가 아니라 '시연용 가짜 데이터'다.
서사: 최근 30일간 여론이 강세(+40)에서 약세(-50)로 이동하고,
      기관은 상대적으로 강세를 유지하는 반면 리테일이 급격히 약세로 돌아서
      기관-리테일 괴리가 확대된다. 후반부 며칠은 쏠림 경보가 켜진다.

사용: python scripts/seed_demo.py [--theme semiconductor]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sentiment_radar.models import KST, utcnow_iso  # noqa: E402
from sentiment_radar.db import get_db  # noqa: E402

NEU = 18.0  # 중립 비중 고정


def _split(nsi: float, neu: float = NEU) -> tuple[float, float, float]:
    """NSI 와 중립 비중으로 (pos, neu, neg) % 산출. pos+neg = 100-neu, pos-neg = nsi."""
    pos = (nsi + (100 - neu)) / 2
    neg = (100 - neu) - pos
    return round(pos, 1), round(neu, 1), round(neg, 1)


def _metrics(nsi_raw: float, nsi_wt: float, n: int, extreme: bool,
             divergence: float | None) -> dict:
    pr = _split(nsi_raw)
    pw = _split(nsi_wt)
    return {
        "n_items": n,
        "pct_pos_raw": pr[0], "pct_neu_raw": pr[1], "pct_neg_raw": pr[2],
        "nsi_raw": round(nsi_raw, 1),
        "pct_pos_wt": pw[0], "pct_neu_wt": pw[1], "pct_neg_wt": pw[2],
        "nsi_wt": round(nsi_wt, 1),
        "extreme_flag": 1 if extreme else 0,
        "divergence": round(divergence, 1) if divergence is not None else None,
    }


def seed(theme: str) -> None:
    today = datetime.now(KST).date()
    days = 30
    now = utcnow_iso()

    with get_db() as db:
        for i in range(days):
            d = (today - timedelta(days=days - 1 - i)).isoformat()
            t = i / (days - 1)
            wig = 6 * math.sin(i * 1.1)  # 잔물결

            all_nsi = 40 - 90 * t + wig
            inst_nsi = 45 - 28 * t + wig * 0.5      # 기관: +45 → +17
            retail_nsi = 35 - 135 * t + wig         # 리테일: +35 → -100
            retail_nsi = max(-100, retail_nsi)
            divergence = inst_nsi - retail_nsi
            n = 60 + int(40 * math.sin(i * 0.7) ** 2)

            extreme_all = abs(all_nsi) >= 60 and t > 0.7
            db.upsert_aggregate(theme, d, "all",
                                _metrics(all_nsi, all_nsi - 8, n, extreme_all, divergence), now)
            db.upsert_aggregate(theme, d, "institutional",
                                _metrics(inst_nsi, inst_nsi + 4, n // 2, False, None), now)
            db.upsert_aggregate(theme, d, "retail",
                                _metrics(retail_nsi, retail_nsi - 6, n // 2,
                                         retail_nsi <= -75, None), now)

            # 소스 타입별 (히트맵용) — 기관군은 강세, 리테일군은 약세
            src_offsets = {
                "report": inst_nsi + 10, "news_kr": inst_nsi, "news_global": inst_nsi - 5,
                "blog": retail_nsi + 8, "youtube": retail_nsi - 10, "reddit": retail_nsi,
            }
            for s, v in src_offsets.items():
                v = max(-100, min(100, v))
                db.upsert_aggregate(theme, d, s,
                                    _metrics(v, v, max(4, n // 6),
                                             abs(v) >= 75, None), now)

            # 가격 (코스피 1001): 2700 → 2500 완만한 하락 + 잔물결
            close = 2700 - 200 * t + 20 * math.sin(i * 0.9)
            prev = 2700 - 200 * ((i - 1) / (days - 1)) + 20 * math.sin((i - 1) * 0.9) if i else close
            ret = (close - prev) / prev * 100 if prev else 0.0
            db.upsert_price(symbol="1001", bucket_date=d, close=round(close, 2),
                            ret=round(ret, 3), collected_at=now)

            # 관심도 (trends_ratio): 0.62 → 0.34 (강세 관심 → 약세 관심)
            ratio = 0.62 - 0.28 * t + 0.03 * math.sin(i * 1.3)
            db.upsert_attention(theme=theme, bucket_date=d, metric="trends_ratio",
                                keyword="반도체 매수|반도체 폭락",
                                value=round(ratio, 3), collected_at=now)

        # 최신일 총평 (반론 Top 3 포함)
        latest = today.isoformat()
        commentary = (
            "최근 여론은 강세에서 약세로 뚜렷이 이동했다. 오늘 가중 NSI는 마이너스 구간에 "
            "진입했고 전주 대비 큰 폭으로 하락했다. 특히 기관은 여전히 중립~강세를 유지하는 "
            "반면 리테일이 급격히 약세로 돌아서며 괴리가 크게 벌어졌다. 이런 극단 쏠림은 "
            "역발상 관점에서 단기 반전 가능성을 경계할 신호이기도 하다. 다음 주 메모리 "
            "고정가 발표와 주요 업체 실적 가이던스를 확인할 필요가 있다. (데모 데이터)"
        )
        counter_args = [
            {"claim": "리테일 공포가 과도하다", "basis": "HBM 수급은 여전히 타이트하다는 소수 리포트"},
            {"claim": "기관 강세가 선행지표일 수 있다", "basis": "과거 괴리 확대 후 지수 반등 사례"},
            {"claim": "관심도 급감은 바닥 신호일 수 있다", "basis": "검색량 저점이 저가 매수 구간과 겹친 이력"},
        ]
        db.upsert_commentary(theme=theme, bucket_date=latest, commentary=commentary,
                             counter_args=json.dumps(counter_args, ensure_ascii=False),
                             model="demo-seed", generated_at=now)

        # 예측 몇 건 (일부 판정 완료)
        preds = [
            (25, "positive", 70, 14), (18, "negative", 80, 14),
            (10, "positive", 65, 14), (3, "negative", 75, 14),
        ]
        for days_ago, view, conf, horizon in preds:
            ed = (today - timedelta(days=days_ago)).isoformat()
            rd = (today - timedelta(days=days_ago - horizon)).isoformat()
            entry = 2700 - 200 * ((days - 1 - days_ago) / (days - 1))
            pid = db.insert_prediction({
                "theme": theme, "created_at": now, "my_view": view,
                "confidence": conf, "horizon_days": horizon, "basis": "데모 예측",
                "target_symbol": "1001", "entry_date": ed,
                "entry_close": round(entry, 2), "resolve_date": rd,
            })
            if rd <= today.isoformat():
                exit_c = 2700 - 200 * ((days - 1 - (days_ago - horizon)) / (days - 1))
                aret = (exit_c - entry) / entry * 100
                hit = (view == "positive" and aret > 0) or (view == "negative" and aret < 0)
                brier = (conf / 100 - (1 if hit else 0)) ** 2
                db.resolve_prediction(pid, exit_close=round(exit_c, 2),
                                      actual_return=round(aret, 2),
                                      outcome="hit" if hit else "miss",
                                      brier=round(brier, 3), resolved_at=now)

    print(f"데모 시드 완료: '{theme}' 최근 {days}일 집계/가격/관심도/총평/예측 생성.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", default="semiconductor")
    seed(ap.parse_args().theme)
