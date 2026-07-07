"""증권사 리포트 수집기 — 네이버금융 리서치 목록 스크레이핑.

robots.txt 준수, 요청 간 2초+ 간격, User-Agent 명시(BaseCollector 정책).
목록 페이지에서 제목 + 증권사 + 작성일을 추출한다. (본문 대신 제목 기반 분류)

주의: 스크레이핑은 사이트 구조 변경에 취약하므로 파싱은 방어적으로 처리하고,
      실패 시 조용히 빈 리스트를 반환한다. fetch_fn 주입으로 테스트 가능.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from ..config import Theme, settings
from ..models import Item
from .base import BaseCollector

log = logging.getLogger(__name__)

_DATE_RE = re.compile(r"(\d{2,4})[.\-/](\d{1,2})[.\-/](\d{1,2})")


def _parse_date(text: str | None) -> str | None:
    if not text:
        return None
    m = _DATE_RE.search(text)
    if not m:
        return None
    y, mo, d = m.groups()
    year = int(y) + 2000 if len(y) == 2 else int(y)
    try:
        return datetime(year, int(mo), int(d), tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def parse_research_list(html: str, base_url: str = "https://finance.naver.com") -> list[dict]:
    """네이버금융 리서치 목록 HTML 파싱 → [{title, url, broker, date}]."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    # 리서치 목록은 table 내 tr. 제목 링크(td.title 혹은 a[href*=read]) 기준으로 탐색.
    for tr in soup.select("tr"):
        a = tr.select_one("a[href*='read'], td.title a, a[href*='research']")
        if not a:
            continue
        title = a.get_text(strip=True)
        if not title:
            continue
        href = a.get("href", "")
        url = href if href.startswith("http") else f"{base_url}/research/{href.lstrip('/')}"
        tds = tr.find_all("td")
        cells = [td.get_text(strip=True) for td in tds]
        # 증권사/작성일 추정: 날짜 형식 셀 = date, 그 앞 텍스트 셀 = broker
        date_iso = None
        broker = ""
        for c in cells:
            if _DATE_RE.search(c):
                date_iso = _parse_date(c)
            elif c and c != title and not broker:
                broker = c
        rows.append({"title": title, "url": url, "broker": broker, "date": date_iso})
    return rows


class NaverReportCollector(BaseCollector):
    source_type = "report"

    def __init__(self, fetch_fn=None) -> None:
        super().__init__()
        cfg = settings().get("sources", {}).get("report", {})
        self.list_url = cfg.get(
            "naver_research_url",
            "https://finance.naver.com/research/market_info_list.naver",
        )
        self.max_pages = int(cfg.get("max_pages", 2))
        self._fetch_fn = fetch_fn            # 테스트 주입용

    @property
    def enabled(self) -> bool:
        return True  # 키 불필요 (스크레이핑)

    def collect(self, theme: Theme) -> list[Item]:
        items: list[Item] = []
        seen: set[str] = set()

        for page in range(1, self.max_pages + 1):
            html = self._fetch_page(page)
            if not html:
                continue
            for row in parse_research_list(html):
                title = row["title"]
                url = row["url"]
                # 리포트는 제목만으로 관련성 판단
                if not self.is_relevant(theme, title):
                    continue
                if url in seen:
                    continue
                seen.add(url)
                items.append(self.finalize(Item(
                    theme=theme.theme, source_type=self.source_type,
                    source_name=row.get("broker") or "naver_research",
                    title=title, content_snippet=title, url=url,
                    author=row.get("broker") or "",
                    published_at=row.get("date"),
                    lang="ko", keyword_matched="",
                )))
                if len(items) >= self.per_source_limit:
                    return items
        return items

    def _fetch_page(self, page: int) -> str:
        if self._fetch_fn is not None:
            return self._fetch_fn(page)
        self.throttle()
        try:
            resp = requests.get(
                self.list_url, params={"page": page},
                headers={"User-Agent": self.user_agent}, timeout=15,
            )
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "euc-kr"
            return resp.text
        except requests.RequestException as e:
            log.error("[report] 페이지 %d 요청 실패: %s", page, e)
            return ""
