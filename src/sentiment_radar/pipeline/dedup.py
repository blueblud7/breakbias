"""중복 제거 — URL 정규화 + 제목 유사도(rapidfuzz).

2단계:
  1) URL 정규화 / content_hash 완전 일치 → 제거 (DB 의 기존 아이템 포함)
  2) 이번 배치 내 제목 유사도가 임계값 이상인 쌍 → 나중 것 제거
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from rapidfuzz import fuzz

from ..config import settings
from ..models import Item
from ..utils.text import content_hash, normalize_url, strip_html

log = logging.getLogger(__name__)


def _norm_title(title: str) -> str:
    return strip_html(title).lower().strip()


def dedup_items(
    items: Iterable[Item],
    *,
    existing_hashes: set[str] | None = None,
    threshold: float | None = None,
) -> list[Item]:
    """중복 제거된 아이템 리스트 반환.

    Args:
        items: 수집된 아이템들
        existing_hashes: DB 에 이미 있는 content_hash 집합 (배치 간 중복 방지)
        threshold: 제목 유사도 임계값 (0~100). None 이면 settings 값 사용.
    """
    if threshold is None:
        threshold = float(settings().get("dedup", {}).get("title_similarity_threshold", 90))
    existing_hashes = set(existing_hashes or set())

    kept: list[Item] = []
    kept_titles: list[str] = []
    seen_hashes: set[str] = set(existing_hashes)
    seen_urls: set[str] = set()

    n_url = n_hash = n_fuzzy = 0

    for item in items:
        # 정규화 값 보정
        url_norm = item.url_normalized or normalize_url(item.url)
        h = item.content_hash or content_hash(item.title, item.url)

        # 1) 완전 일치 (해시 / URL)
        if h in seen_hashes:
            n_hash += 1
            continue
        if url_norm and url_norm in seen_urls:
            n_url += 1
            continue

        # 2) 제목 유사도
        title_n = _norm_title(item.title)
        is_dup = False
        if title_n:
            for kt in kept_titles:
                if fuzz.token_set_ratio(title_n, kt) >= threshold:
                    is_dup = True
                    break
        if is_dup:
            n_fuzzy += 1
            continue

        # 통과 → 유지
        item.url_normalized = url_norm
        item.content_hash = h
        kept.append(item)
        kept_titles.append(title_n)
        seen_hashes.add(h)
        if url_norm:
            seen_urls.add(url_norm)

    log.info(
        "dedup: 입력=%d 유지=%d 제거(url=%d, hash=%d, 유사=%d)",
        len(kept) + n_url + n_hash + n_fuzzy, len(kept), n_url, n_hash, n_fuzzy,
    )
    return kept
