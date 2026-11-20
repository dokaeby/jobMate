"""보안 유틸 — API 키 인증 헬퍼 + Fernet 기반 저장 데이터 암호화.

암호화 키 우선순위:
1. JOBMATE_ENCRYPTION_KEY (Fernet 키 또는 임의 문자열 → SHA256 파생)
2. JOBMATE_API_KEY / CRON_SECRET / JOBMATE_CRON_SECRET (파생 재료)
키 자료가 없으면 암호화 없이 평문 저장(접두사 없음) — 로컬 개발 호환.
암호화된 값은 "enc:v1:" 접두사로 표시해 복호화 여부를 구분한다.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from typing import Any

logger = logging.getLogger("jobmate.security")

PREFIX = "enc:v1:"
ENCRYPTION_KEY_ENV = "JOBMATE_ENCRYPTION_KEY"
API_KEY_ENV = "JOBMATE_API_KEY"
_CRON_SECRET_ENVS = ("CRON_SECRET", "JOBMATE_CRON_SECRET")


def _key_material() -> str:
    return (
        os.environ.get(ENCRYPTION_KEY_ENV, "")
        or os.environ.get(API_KEY_ENV, "")
        or os.environ.get(_CRON_SECRET_ENVS[0], "")
        or os.environ.get(_CRON_SECRET_ENVS[1], "")
    )


def _fernet() -> Any | None:
    """설정된 키로 Fernet 생성. 유효한 Fernet 키면 그대로, 아니면 SHA256 파생."""
    material = _key_material()
    if not material:
        return None
    from cryptography.fernet import Fernet

    try:
        return Fernet(material.encode())
    except Exception:
        derived = base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest())
        return Fernet(derived)


def encrypt_str(value: str) -> str:
    """평문 → "enc:v1:" + Fernet 토큰. 키 없으면 평문 그대로."""
    if not value:
        return value
    f = _fernet()
    if f is None:
        return value
    return PREFIX + f.encrypt(value.encode()).decode()


def decrypt_str(value: str) -> str:
    """ "enc:v1:" 접두사 값만 복호화. 평문/키 없음/복호화 실패 시 원문 반환."""
    if not value or not value.startswith(PREFIX):
        return value
    f = _fernet()
    if f is None:
        logger.warning("encrypted value found but no key material configured")
        return value
    try:
        from cryptography.fernet import InvalidToken

        return f.decrypt(value[len(PREFIX) :].encode()).decode()
    except InvalidToken:
        logger.warning("failed to decrypt value (wrong key?)")
        return value
    except Exception as e:
        logger.warning("decrypt error: %s", e)
        return value


def api_key() -> str:
    """설정된 API 키. 비어 있으면 인증 비활성(로컬 개발)."""
    return os.environ.get(API_KEY_ENV, "")


def authorized(request: Any) -> bool:
    """Authorization: Bearer <key> 또는 X-API-Key 헤더 검증."""
    key = api_key()
    if not key:
        return True
    auth = request.headers.get("authorization", "")
    provided = request.headers.get("x-api-key", "")
    if auth.lower().startswith("bearer "):
        provided = auth[7:].strip() or provided
    return provided == key
