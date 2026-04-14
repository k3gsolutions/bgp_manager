from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

from ..config import settings


def create_access_token(*, subject: str, extra: dict[str, Any] | None = None) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=settings.jwt_expire_minutes)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def parse_user_id_from_token(token: str) -> int | None:
    try:
        data = decode_token(token)
        uid = data.get("sub")
        if uid is None:
            return None
        return int(uid)
    except (JWTError, ValueError, TypeError):
        return None


def audit_claims_from_authorization_header(authorization: str | None) -> tuple[int | None, str | None, str | None]:
    """Extrai ``user_id``, ``username`` e ``role`` do Bearer JWT (sem exceções)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None, None, None
    token = authorization[7:].strip()
    if not token:
        return None, None, None
    try:
        data = decode_token(token)
        uid = data.get("sub")
        user_id = int(uid) if uid is not None else None
    except (JWTError, ValueError, TypeError):
        return None, None, None
    uname = data.get("username")
    if not isinstance(uname, str):
        uname = None
    role = data.get("role")
    if isinstance(role, str):
        role = role.lower()
    else:
        role = None
    return user_id, uname, role
