"""Slack/Telegram 웹훅 서명 검증 — calendar-agent message_listeners 포팅."""

from __future__ import annotations

import hashlib
import hmac
import time


def verify_slack_signature(body: bytes, timestamp: str, signature: str, secret: str) -> bool:
    """Slack 서명 + 5분 유효시간 검사. 원본 바이트 기준 HMAC."""
    if not secret:
        return False
    try:
        if not timestamp.isascii() or not timestamp.isdigit():
            return False
        if abs(time.time() - int(timestamp)) > 300:
            return False
    except ValueError:
        return False
    expected = (
        "v0="
        + hmac.new(
            secret.encode(), b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256
        ).hexdigest()
    )
    return hmac.compare_digest(expected.encode(), signature.encode())


def verify_telegram_secret(provided: str, secret: str) -> bool:
    """Telegram webhook secret token 비교."""
    if not secret:
        return False
    return hmac.compare_digest(provided.encode(), secret.encode())


def allowed_identifier(identifier: str, configured: str) -> bool:
    """허용 목록(쉼표 구분). 비어 있으면 전체 허용."""
    allowed = {item.strip() for item in configured.split(",") if item.strip()}
    return not allowed or identifier in allowed
