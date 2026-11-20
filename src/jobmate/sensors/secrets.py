"""시크릿 패턴 스캔 — 결정적."""

from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("gcp_key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    (
        "generic_secret",
        re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[^\s'\"]{8,}"),
    ),
]


def scan_secrets(text: str) -> list[str]:
    """매칭된 패턴 이름 목록을 반환한다(실제 비밀값은 반환하지 않음)."""
    hits = []
    for name, pat in _PATTERNS:
        if pat.search(text):
            hits.append(name)
    return hits
