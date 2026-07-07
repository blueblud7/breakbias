"""알림 전송 — 텔레그램 봇 (M5/M6). 미설정 시 로그로 대체.

주입 가능한 send 콜백으로 테스트 지원.
"""

from __future__ import annotations

import logging
from typing import Callable

import requests

from .config import env

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, send_fn: Callable[[str], bool] | None = None) -> None:
        self._send = send_fn or self._telegram_send

    def send(self, message: str) -> bool:
        return self._send(message)

    def _telegram_send(self, message: str) -> bool:
        token = env("TELEGRAM_BOT_TOKEN")
        chat_id = env("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            log.info("[notify] 텔레그램 미설정 — 로그로 대체:\n%s", message)
            return False
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message,
                      "disable_web_page_preview": True},
                timeout=10,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            log.error("[notify] 텔레그램 전송 실패: %s", e)
            return False
        return True
