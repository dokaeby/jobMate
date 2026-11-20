"""로컬 실행용 실용 어댑터 — 로그 기록 notifier 등."""

from __future__ import annotations

import logging

logger = logging.getLogger("jobmate.adapters.local")


class LoggingNotifier:
    """Notifier 프로토콜 — 로그에만 기록 (로컬 개발용)."""

    def send(self, *, title: str, body: str, source_url: str) -> None:
        logger.info("notify: %s | %s | %s", title, body[:80], source_url)
