"""간단한 토큰 인증. AUTH_ENABLED=false(기본)이면 검사하지 않는다.

- 관리자 API(GUI가 부름):   Authorization: Bearer <ADMIN_TOKEN>
- 장치 API(PC2·낙상 모듈): Authorization: Bearer <DEVICE_TOKEN>
"""
from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import settings

_bearer = HTTPBearer(auto_error=False)


def _check(expected: str, creds: HTTPAuthorizationCredentials | None) -> None:
    if not settings.auth_enabled:
        return
    if not expected or not creds or not secrets.compare_digest(creds.credentials, expected):
        raise HTTPException(401, "인증 토큰이 없거나 틀립니다")


def admin(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    _check(settings.admin_token, creds)


def device(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    _check(settings.device_token, creds)
