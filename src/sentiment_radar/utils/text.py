"""텍스트/URL 유틸 — 정규화, 해시, HTML 정리."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

# 트래킹/세션 파라미터 (URL 정규화 시 제거)
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "igshid", "spm", "cmpid", "ref", "referer", "referrer",
    "sid", "sessionid", "session_id",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text: str | None) -> str:
    """HTML 태그 제거 + 공백 정리 (Naver API 응답의 <b> 등 처리)."""
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    # 일부 엔티티 간단 치환
    text = (
        text.replace("&quot;", '"')
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#39;", "'")
        .replace("&apos;", "'")
        .replace("&nbsp;", " ")
    )
    return _WS_RE.sub(" ", text).strip()


def normalize_url(url: str | None) -> str:
    """URL 정규화: 소문자 스킴/호스트, 트래킹 파라미터 제거, 끝 슬래시 정리."""
    if not url:
        return ""
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return url

    # http/https 는 동일 문서로 간주 (중복 제거 목적) → https 로 통일
    scheme = parts.scheme.lower() or "https"
    if scheme == "http":
        scheme = "https"
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    # 쿼리에서 트래킹 파라미터 제거 후 정렬
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS
    ]
    query = urlencode(sorted(query_pairs))

    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, query, ""))  # fragment 제거


def content_hash(title: str, url: str = "") -> str:
    """제목(정규화) + 정규화 URL 기반 안정적 해시."""
    norm_title = _WS_RE.sub(" ", strip_html(title).lower()).strip()
    basis = f"{norm_title}|{normalize_url(url)}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def truncate(text: str | None, limit: int) -> str:
    if not text:
        return ""
    return text[:limit]
