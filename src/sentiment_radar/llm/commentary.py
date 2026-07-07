"""deepseek-v4-pro 일별 총평 (M4).

집계 결과 + 대표 아이템 요약 20개(긍/중/부 골고루)를 입력으로 하루 1회 총평 생성.
강제 섹션: "다수 의견에 대한 반론 Top 3" — 이 시스템의 존재 이유.
금지: 매수/매도 추천.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from ..config import env, settings
from ..models import to_kst_date, utcnow_iso

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "너는 냉정한 시장 전략가다. 오늘의 센티먼트 집계 데이터를 보고 총평을 작성한다.\n"
    "반드시 JSON 만 출력한다. 스키마:\n"
    "{\n"
    '  "commentary": "총평 본문(한국어 500자 내외). 다음을 포함: '
    "①오늘 분포와 전일/전주 대비 변화 ②기관 vs 리테일 괴리 해석 "
    "③쏠림 경보 시 역발상 관점 ④확인해야 할 다음 이벤트/데이터\",\n"
    '  "counter_arguments": [{"claim":"다수 뷰가 틀릴 수 있는 논거","basis":"소수의견 근거"}, '
    "3개],\n"
    '  "next_events": ["확인할 이벤트/데이터", ...]\n'
    "}\n"
    "counter_arguments 는 반드시 3개. 현재 다수 뷰와 '반대' 방향의 논거를 "
    "소수 의견 아이템에서 추출하라.\n"
    "금지: 매수/매도 추천. 이 시스템은 뷰 정량화 도구이지 투자자문이 아니다."
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _pct(row, key) -> float:
    return round(float(row[key]), 1) if row and row[key] is not None else 0.0


def build_context(db, theme: str, date: str) -> dict[str, Any]:
    """총평 입력 컨텍스트 구성."""
    from datetime import datetime, timedelta

    today = db.fetch_aggregate_one(theme, date, "all")
    inst = db.fetch_aggregate_one(theme, date, "institutional")
    retail = db.fetch_aggregate_one(theme, date, "retail")

    d = datetime.strptime(date, "%Y-%m-%d").date()
    yday = db.fetch_aggregate_one(theme, (d - timedelta(days=1)).isoformat(), "all")
    lweek = db.fetch_aggregate_one(theme, (d - timedelta(days=7)).isoformat(), "all")

    # 대표 아이템 요약 (긍/중/부 골고루, 최대 20)
    reps: dict[str, list[str]] = {"positive": [], "neutral": [], "negative": []}
    for r in db.fetch_classified_detailed(theme):
        rd = to_kst_date(r["published_at"]) or to_kst_date(r["collected_at"])
        if rd != date:
            continue
        s = r["sentiment"]
        if s in reps and len(reps[s]) < 7:
            summ = r["one_line_summary"] or r["title"]
            reps[s].append(f"[{r['source_type']}] {summ}")

    return {
        "date": date,
        "today": today, "inst": inst, "retail": retail,
        "yday": yday, "lweek": lweek,
        "representatives": reps,
    }


def format_user_prompt(ctx: dict[str, Any]) -> str:
    t = ctx["today"]
    lines = [f"[날짜] {ctx['date']}"]
    if t:
        lines.append(
            f"[오늘 분포] 긍정 {_pct(t,'pct_pos_wt')}% / 중립 {_pct(t,'pct_neu_wt')}% / "
            f"부정 {_pct(t,'pct_neg_wt')}% (가중), NSI {_pct(t,'nsi_wt')}"
        )
        if t["extreme_flag"]:
            lines.append("[쏠림 경보] 발생 (한 방향 75% 이상)")
    if ctx["yday"]:
        lines.append(f"[전일 NSI] {_pct(ctx['yday'],'nsi_wt')}")
    if ctx["lweek"]:
        lines.append(f"[전주 NSI] {_pct(ctx['lweek'],'nsi_wt')}")
    if ctx["inst"] and ctx["retail"]:
        lines.append(
            f"[기관 NSI] {_pct(ctx['inst'],'nsi_wt')}  vs  "
            f"[리테일 NSI] {_pct(ctx['retail'],'nsi_wt')}  "
            f"(괴리 {_pct(t,'divergence') if t else 0})"
        )
    lines.append("\n[대표 의견]")
    for s, label in [("positive", "긍정"), ("neutral", "중립"), ("negative", "부정")]:
        for item in ctx["representatives"].get(s, []):
            lines.append(f"  ({label}) {item}")
    return "\n".join(lines)


def parse_commentary(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", cleaned).strip()
    raw = None
    try:
        raw = json.loads(cleaned)
    except json.JSONDecodeError:
        m = _JSON_RE.search(cleaned)
        if m:
            try:
                raw = json.loads(m.group(0))
            except json.JSONDecodeError:
                raw = None
    if not isinstance(raw, dict):
        # JSON 실패 시 원문을 통째로 총평으로 (반론은 비움)
        return {"commentary": text.strip()[:2000], "counter_arguments": [],
                "next_events": []}

    cas = raw.get("counter_arguments", [])
    norm_cas = []
    if isinstance(cas, list):
        for c in cas[:3]:
            if isinstance(c, dict):
                norm_cas.append({"claim": str(c.get("claim", "")).strip(),
                                 "basis": str(c.get("basis", "")).strip()})
            else:
                norm_cas.append({"claim": str(c).strip(), "basis": ""})
    return {
        "commentary": str(raw.get("commentary", "")).strip(),
        "counter_arguments": norm_cas,
        "next_events": [str(x).strip() for x in raw.get("next_events", [])
                        if str(x).strip()][:5],
    }


class CommentaryGenerator:
    def __init__(self, model: str | None = None,
                 complete_fn: Callable[[str, str], tuple[str, int, int]] | None = None) -> None:
        self.model = model or env("DEEPSEEK_MODEL", "deepseek-v4-pro")
        self._complete = complete_fn or self._make_deepseek_complete()

    @property
    def enabled(self) -> bool:
        return self._complete is not None

    def _make_deepseek_complete(self):
        api_key = env("DEEPSEEK_API_KEY")
        if not api_key:
            log.warning("[commentary] DEEPSEEK_API_KEY 미설정 — 총평 비활성")
            return None
        try:
            from openai import OpenAI
        except ImportError:
            log.warning("[commentary] openai 패키지 미설치 — 총평 비활성")
            return None
        base_url = env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        client = OpenAI(api_key=api_key, base_url=base_url)
        model = self.model

        def _complete(system: str, user: str) -> tuple[str, int, int]:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                temperature=0.3,
            )
            text = resp.choices[0].message.content or ""
            u = resp.usage
            return text, getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0

        return _complete

    def generate(self, db, theme: str, date: str) -> dict[str, Any] | None:
        """총평 생성 후 daily_commentary 저장. 결과 dict 반환(비활성 시 None)."""
        if not self.enabled:
            log.warning("[commentary] 비활성 — 스킵")
            return None
        ctx = build_context(db, theme, date)
        if ctx["today"] is None:
            log.warning("[commentary] %s 집계 없음 — 스킵", date)
            return None
        user = format_user_prompt(ctx)
        try:
            text, pt, ct = self._complete(SYSTEM_PROMPT, user)
        except Exception as e:
            log.error("[commentary] API 실패: %s", e)
            return None

        parsed = parse_commentary(text)
        if parsed is None:
            return None

        from ..llm.cost import CostTracker
        CostTracker(db, self.model, call_type="commentary").record(pt, ct, 1)
        db.upsert_commentary(
            theme=theme, bucket_date=date, commentary=parsed["commentary"],
            counter_args=json.dumps(parsed["counter_arguments"], ensure_ascii=False),
            model=self.model, generated_at=utcnow_iso(),
        )
        log.info("[commentary] %s 총평 저장 (반론 %d개)", date, len(parsed["counter_arguments"]))
        return parsed
