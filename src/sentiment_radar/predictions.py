"""예측 일지 (M6) — 확증편향 교정 코어.

오늘 데이터를 보기 '전에' 내 뷰를 기록 → 만기에 실제 지수 수익률과 대조 →
적중 판정 → Brier score 누적 + 캘리브레이션.

Brier score: p=확신도(0~1), o=적중(1)/실패(0) → (p - o)^2. 낮을수록 좋음.
방향성 예측이므로 '적중'은 예측 방향과 실제 수익률 부호 일치로 정의한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import KST, utcnow_iso
from .prices import MarketDataProvider, PriceProvider

log = logging.getLogger(__name__)


def _kst_today() -> str:
    return datetime.now(KST).date().isoformat()


def _add_days(date_str: str, days: int) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    return (d + timedelta(days=days)).isoformat()


@dataclass
class BrierSummary:
    n: int = 0
    brier_mean: float | None = None
    hit_rate: float | None = None
    recent_n: int = 0
    recent_hit_rate: float | None = None


class PredictionJournal:
    def __init__(self, db, price_provider: PriceProvider | None = None) -> None:
        self.db = db
        self.prices = price_provider or MarketDataProvider()

    # --- 기록 ---
    def add(self, *, theme: str, my_view: str, confidence: float,
            horizon_days: int, basis: str, target_symbol: str,
            entry_date: str | None = None) -> int:
        if my_view not in {"positive", "negative"}:
            raise ValueError("my_view 는 positive|negative 만 허용")
        if not (50 <= confidence <= 100):
            raise ValueError("confidence 는 50~100(%) 범위")

        entry_date = entry_date or _kst_today()
        entry_close = self.prices.get_close(target_symbol, entry_date)
        if entry_close is None:
            log.warning("[predict] 기준가 조회 실패(%s@%s) — 판정 시 재시도",
                        target_symbol, entry_date)

        row = {
            "theme": theme, "created_at": utcnow_iso(), "my_view": my_view,
            "confidence": float(confidence), "horizon_days": int(horizon_days),
            "basis": basis, "target_symbol": target_symbol,
            "entry_date": entry_date, "entry_close": entry_close,
            "resolve_date": _add_days(entry_date, horizon_days),
        }
        pid = self.db.insert_prediction(row)
        log.info("[predict] #%d 기록: %s %s conf=%.0f%% ~%s",
                 pid, theme, my_view, confidence, row["resolve_date"])
        return pid

    # --- 판정 ---
    def resolve_due(self, as_of: str | None = None) -> list[dict]:
        """만기 도래 예측을 실제 수익률과 대조해 판정. 판정된 목록 반환."""
        as_of = as_of or _kst_today()
        resolved = []
        for p in self.db.fetch_due_predictions(as_of):
            entry_close = p["entry_close"]
            if entry_close is None:  # 기록 시 실패했으면 지금 재조회
                entry_close = self.prices.get_close(p["target_symbol"], p["entry_date"])
            exit_close = self.prices.get_close(p["target_symbol"], p["resolve_date"])
            if entry_close is None or exit_close is None or entry_close == 0:
                log.warning("[predict] #%d 가격 미확보 — 판정 보류", p["id"])
                continue

            actual_return = (exit_close - entry_close) / entry_close * 100.0
            hit = (
                (p["my_view"] == "positive" and actual_return > 0)
                or (p["my_view"] == "negative" and actual_return < 0)
            )
            prob = p["confidence"] / 100.0
            brier = (prob - (1.0 if hit else 0.0)) ** 2
            self.db.resolve_prediction(
                p["id"], exit_close=exit_close, actual_return=actual_return,
                outcome="hit" if hit else "miss", brier=brier,
                resolved_at=utcnow_iso(),
            )
            resolved.append({"id": p["id"], "outcome": "hit" if hit else "miss",
                             "actual_return": actual_return, "brier": brier})
            log.info("[predict] #%d 판정: %s (수익률 %.2f%%, Brier %.3f)",
                     p["id"], "적중" if hit else "빗나감", actual_return, brier)
        return resolved

    # --- 지표 ---
    def brier_summary(self, theme: str | None = None, recent: int = 20) -> BrierSummary:
        rows = self.db.fetch_predictions(theme, resolved_only=True)
        if not rows:
            return BrierSummary()
        briers = [r["brier"] for r in rows if r["brier"] is not None]
        hits = [1 if r["outcome"] == "hit" else 0 for r in rows]
        # rows 는 created_at DESC → 앞쪽이 최신
        recent_hits = hits[:recent]
        return BrierSummary(
            n=len(rows),
            brier_mean=sum(briers) / len(briers) if briers else None,
            hit_rate=sum(hits) / len(hits) if hits else None,
            recent_n=len(recent_hits),
            recent_hit_rate=(sum(recent_hits) / len(recent_hits)) if recent_hits else None,
        )

    def calibration(self, theme: str | None = None) -> list[dict]:
        """확신도 구간별 (평균 확신도 vs 실제 적중률) — 과신 진단용."""
        bins = [(50, 60), (60, 70), (70, 80), (80, 90), (90, 101)]
        rows = self.db.fetch_predictions(theme, resolved_only=True)
        out = []
        for lo, hi in bins:
            grp = [r for r in rows if lo <= r["confidence"] < hi]
            if not grp:
                out.append({"bin": f"{lo}-{min(hi,100)}", "n": 0,
                            "avg_confidence": None, "actual_hit_rate": None})
                continue
            avg_conf = sum(r["confidence"] for r in grp) / len(grp) / 100.0
            hit_rate = sum(1 for r in grp if r["outcome"] == "hit") / len(grp)
            out.append({"bin": f"{lo}-{min(hi,100)}", "n": len(grp),
                        "avg_confidence": avg_conf, "actual_hit_rate": hit_rate})
        return out
