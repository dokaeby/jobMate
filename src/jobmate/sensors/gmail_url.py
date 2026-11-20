"""Gmail 원본 링크 안전 생성 — 임의 리다이렉트 차단."""

from __future__ import annotations

ALLOWED_HOSTS = {"mail.google.com"}


def build_gmail_url(message_id: str, connection_id: str = "default") -> str:
    if not message_id or not message_id.isalnum() and not message_id.replace("-", "").isalnum():
        raise ValueError("invalid gmail message id")
    return f"https://mail.google.com/mail/u/0/#all/{message_id}"


def is_safe_source_url(url: str) -> bool:
    from urllib.parse import urlparse

    p = urlparse(url)
    return p.scheme == "https" and p.hostname in ALLOWED_HOSTS
