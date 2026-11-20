"""Web Push 발송 어댑터 — 저장된 구독으로 pywebpush 전송."""

from __future__ import annotations

import json
import logging
from typing import Any

from pywebpush import WebPushException, webpush

logger = logging.getLogger("jobmate.adapters.push")


class WebPushNotifier:
    """Notifier 프로토콜 — push_subscriptions 전체로 Web Push 발송.

    VAPID_PRIVATE_KEY 미설정 또는 구독 없음이면 조용히 no-op.
    404/410 응답 구독은 만료로 간주해 삭제한다.
    """

    def __init__(
        self, store: Any, *, private_key: str, subject: str = "mailto:admin@localhost"
    ) -> None:
        self._store = store
        self._private_key = private_key
        self._subject = subject

    def send(self, *, title: str, body: str, source_url: str) -> None:
        if not self._private_key:
            logger.debug("webpush skipped: VAPID_PRIVATE_KEY not set")
            return
        subs = self._store.list_push_subscriptions()
        if not subs:
            return
        payload = json.dumps({"title": title, "body": body, "url": source_url or "/"})
        stale: list[str] = []
        for sub in subs:
            info = {"endpoint": sub["endpoint"], "keys": sub["keys"]}
            try:
                webpush(
                    subscription_info=info,
                    data=payload,
                    vapid_private_key=self._private_key,
                    vapid_claims={"sub": self._subject},
                )
            except WebPushException as e:
                status = getattr(e.response, "status_code", None)
                if status in (404, 410):
                    stale.append(sub["endpoint"])
                logger.warning("webpush failed: %s (%s)", sub["endpoint"][:60], e)
            except Exception as e:
                logger.warning("webpush error: %s (%s)", sub["endpoint"][:60], e)
        for endpoint in stale:
            self._store.delete_push_subscription(endpoint)
