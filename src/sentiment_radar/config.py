"""설정 로더 — settings.yaml + themes/*.yaml + .env 를 통합해서 제공."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# 프로젝트 루트 (이 파일 기준 3단계 위: src/sentiment_radar/config.py -> repo root)
ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
THEMES_DIR = CONFIG_DIR / "themes"

load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Theme:
    """단일 테마 설정."""

    theme: str
    display_name: str
    my_view: str
    keywords_ko: list[str]
    keywords_en: list[str]
    relevance_must_include_any: list[str]
    price_symbols: dict[str, str] = field(default_factory=dict)
    trends_pairs: list[dict[str, str]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def all_keywords(self) -> list[str]:
        return list(self.keywords_ko) + list(self.keywords_en)


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def settings() -> dict[str, Any]:
    """전역 settings.yaml 반환 (캐시)."""
    return _read_yaml(CONFIG_DIR / "settings.yaml")


def load_theme(name: str) -> Theme:
    """테마 이름(파일명 또는 display_name)으로 Theme 로드."""
    # 파일명 직매칭 우선, 없으면 display_name 스캔
    candidate = THEMES_DIR / f"{name}.yaml"
    if not candidate.exists():
        for p in THEMES_DIR.glob("*.yaml"):
            data = _read_yaml(p)
            if data.get("theme") == name or data.get("display_name") == name:
                candidate = p
                break
    if not candidate.exists():
        raise FileNotFoundError(
            f"테마 '{name}' 를 찾을 수 없습니다. {THEMES_DIR} 확인."
        )

    data = _read_yaml(candidate)
    return Theme(
        theme=data["theme"],
        display_name=data.get("display_name", data["theme"]),
        my_view=data.get("my_view", "none"),
        keywords_ko=data.get("keywords_ko", []),
        keywords_en=data.get("keywords_en", []),
        relevance_must_include_any=data.get("relevance_must_include_any", []),
        price_symbols=data.get("price_symbols", {}),
        trends_pairs=data.get("trends_pairs", []),
        raw=data,
    )


def list_themes() -> list[str]:
    """등록된 테마 이름 목록."""
    return sorted(p.stem for p in THEMES_DIR.glob("*.yaml"))


def env(key: str, default: str | None = None) -> str | None:
    """환경변수 조회 헬퍼."""
    return os.getenv(key, default)


def require_env(key: str) -> str:
    """필수 환경변수 조회 — 없으면 명확한 에러."""
    val = os.getenv(key)
    if not val:
        raise RuntimeError(
            f"환경변수 {key} 가 설정되지 않았습니다. .env 파일을 확인하세요 "
            f"(.env.example 참고)."
        )
    return val


def db_path() -> Path:
    """DB 파일 경로."""
    p = os.getenv("DB_PATH", "data/sentiment.db")
    path = Path(p)
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
