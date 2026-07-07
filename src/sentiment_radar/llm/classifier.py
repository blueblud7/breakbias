"""gpt-5-nano 개별 센티먼트 분류기.

- 아이템당 1회 호출, JSON only 응답
- 파싱 실패 시 1회 재시도
- 방어적 파싱: 코드펜스/잡텍스트 제거, enum 검증, confidence 클램핑
- OpenAI SDK 사용 (미설치/키 없음이면 enabled=False)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from ..config import env, settings
from ..models import SENTIMENTS, TIME_HORIZONS

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "너는 금융 센티먼트 분류기다. 주어진 텍스트가 [{theme}] 에 대해 갖는 뷰를 분류하라.\n"
    "JSON만 출력한다. 다른 말/설명/코드펜스 금지.\n"
    "스키마:\n"
    "{{\n"
    '  "sentiment": "positive" | "neutral" | "negative",\n'
    '  "confidence": 0.0~1.0,\n'
    '  "one_line_summary": "한 줄 요약 (한국어)",\n'
    '  "key_argument": "핵심 논거 한 문장",\n'
    '  "time_horizon": "short" | "mid" | "long" | "unclear",\n'
    '  "is_opinion": true/false\n'
    "}}\n"
    "주의: 주가 하락 '보도'와 하락 '전망'을 구분하라. "
    '"폭락했다"는 사실보도(neutral, is_opinion=false), '
    '"더 떨어질 것"은 전망(negative, is_opinion=true)이다. '
    "단순 가격 등락 중계는 is_opinion=false. "
    "클릭베이트 제목의 과장은 본문 기준으로 판단하라."
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def build_user_prompt(title: str, snippet: str, source_type: str) -> str:
    return (
        f"[소스] {source_type}\n"
        f"[제목] {title}\n"
        f"[본문] {snippet or '(본문 없음)'}"
    )


def _coerce_confidence(val: Any) -> float:
    try:
        c = float(val)
    except (TypeError, ValueError):
        return 0.5
    if c > 1.0:  # 80 처럼 퍼센트로 준 경우
        c = c / 100.0
    return max(0.0, min(1.0, c))


def parse_classification(text: str | None) -> dict[str, Any] | None:
    """LLM 원문에서 분류 dict 를 방어적으로 추출. 실패 시 None."""
    if not text:
        return None
    # 코드펜스 제거
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", cleaned).strip()

    raw: dict[str, Any] | None = None
    try:
        raw = json.loads(cleaned)
    except json.JSONDecodeError:
        m = _JSON_RE.search(cleaned)
        if m:
            try:
                raw = json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    if not isinstance(raw, dict):
        return None

    sentiment = str(raw.get("sentiment", "")).lower().strip()
    if sentiment not in SENTIMENTS:
        # 흔한 변형 매핑
        sentiment = {"pos": "positive", "neg": "negative", "neu": "neutral"}.get(
            sentiment, "neutral"
        )
    horizon = str(raw.get("time_horizon", "unclear")).lower().strip()
    if horizon not in TIME_HORIZONS:
        horizon = "unclear"

    is_opinion = raw.get("is_opinion", True)
    if isinstance(is_opinion, str):
        is_opinion = is_opinion.strip().lower() in {"true", "1", "yes", "y"}

    return {
        "sentiment": sentiment,
        "confidence": _coerce_confidence(raw.get("confidence", 0.5)),
        "one_line_summary": str(raw.get("one_line_summary", "")).strip()[:300],
        "key_argument": str(raw.get("key_argument", "")).strip()[:500],
        "time_horizon": horizon,
        "is_opinion": bool(is_opinion),
    }


class Classifier:
    """gpt-5-nano 분류기.

    `complete_fn` 을 주입하면 실제 API 대신 사용(테스트용).
    complete_fn(system, user) -> (text, prompt_tokens, completion_tokens)
    """

    def __init__(
        self,
        model: str | None = None,
        complete_fn: Callable[[str, str], tuple[str, int, int]] | None = None,
    ) -> None:
        self.model = model or env("OPENAI_MODEL", "gpt-5-nano")
        self.max_retries = int(settings().get("llm", {}).get("classify_max_retries", 1))
        self._complete = complete_fn or self._make_openai_complete()

    @property
    def enabled(self) -> bool:
        return self._complete is not None

    def _make_openai_complete(self):
        api_key = env("OPENAI_API_KEY")
        if not api_key:
            log.warning("[classifier] OPENAI_API_KEY 미설정 — 분류 비활성")
            return None
        try:
            from openai import OpenAI
        except ImportError:
            log.warning("[classifier] openai 패키지 미설치 — 분류 비활성")
            return None

        client = OpenAI(api_key=api_key)
        model = self.model

        def _complete(system: str, user: str) -> tuple[str, int, int]:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
            text = resp.choices[0].message.content or ""
            usage = resp.usage
            pt = getattr(usage, "prompt_tokens", 0) or 0
            ct = getattr(usage, "completion_tokens", 0) or 0
            return text, pt, ct

        return _complete

    def classify(
        self, *, theme_name: str, title: str, snippet: str, source_type: str
    ) -> tuple[dict[str, Any] | None, int, int]:
        """분류 실행. (parsed_dict|None, prompt_tokens, completion_tokens) 반환."""
        if not self.enabled:
            return None, 0, 0
        system = SYSTEM_PROMPT.format(theme=theme_name)
        user = build_user_prompt(title, snippet, source_type)

        total_pt = total_ct = 0
        for attempt in range(self.max_retries + 1):
            try:
                text, pt, ct = self._complete(system, user)
            except Exception as e:  # API 오류 방어
                log.error("[classifier] API 호출 실패(attempt %d): %s", attempt, e)
                return None, total_pt, total_ct
            total_pt += pt
            total_ct += ct
            parsed = parse_classification(text)
            if parsed is not None:
                return parsed, total_pt, total_ct
            log.warning("[classifier] JSON 파싱 실패(attempt %d) — 재시도", attempt)
        return None, total_pt, total_ct
