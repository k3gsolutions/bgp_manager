"""Auditoria de ações de utilizadores (JSONL, ficheiro dedicado).

Separado de:

- ``logs/bgpmanager.log`` — logger da aplicação (mensagens técnicas).
- ``logs/events.log`` — eventos de equipamento / coleta (aba Logs na UI).

Este módulo grava em ``logs/audit/audit.log`` (rotação própria).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .activity_log import _gzip_namer, _gzip_rotator

_audit_logger = logging.getLogger("bgpmanager.audit")

_LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
_AUDIT_DIR = _LOG_DIR / "audit"
_AUDIT_FILE = _AUDIT_DIR / "audit.log"

_MAX_QUERY_LEN = 2048


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(s: str | None, max_len: int) -> str | None:
    if s is None:
        return None
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


def configure_audit_logging() -> None:
    if _audit_logger.handlers:
        return
    _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(
        _AUDIT_FILE,
        maxBytes=5_000_000,
        backupCount=14,
        encoding="utf-8",
    )
    h.namer = _gzip_namer
    h.rotator = _gzip_rotator
    h.setFormatter(logging.Formatter("%(message)s"))
    _audit_logger.addHandler(h)
    _audit_logger.setLevel(logging.INFO)
    _audit_logger.propagate = False


def write_audit_record(payload: dict[str, Any]) -> None:
    """Escreve uma linha JSON (sem secrets)."""
    row = {"ts": _utc_now_iso(), **payload}
    _audit_logger.info(json.dumps(row, ensure_ascii=False, default=str))


def log_http_audit(
    *,
    user_id: int | None,
    username: str | None,
    role: str | None,
    method: str,
    path: str,
    query: str | None,
    status_code: int,
    duration_ms: int,
    client_ip: str | None,
) -> None:
    write_audit_record(
        {
            "event": "http_request",
            "user_id": user_id,
            "username": username,
            "role": role,
            "method": method,
            "path": path,
            "query": _truncate(query, _MAX_QUERY_LEN) or None,
            "status_code": status_code,
            "duration_ms": duration_ms,
            "client_ip": client_ip,
        }
    )


def log_login_success(
    *,
    user_id: int,
    username: str,
    role: str,
    client_ip: str | None,
) -> None:
    write_audit_record(
        {
            "event": "login_success",
            "user_id": user_id,
            "username": username,
            "role": role,
            "client_ip": client_ip,
        }
    )


def log_login_failure(
    *,
    username: str,
    reason: str,
    client_ip: str | None,
) -> None:
    write_audit_record(
        {
            "event": "login_failure",
            "user_id": None,
            "username": username,
            "reason": reason,
            "client_ip": client_ip,
        }
    )


def log_user_consultation(
    *,
    user_id: int,
    username: str | None,
    role: str | None,
    consultation: str,
    device_id: int | None = None,
    detail: dict[str, Any] | None = None,
    client_ip: str | None = None,
) -> None:
    """Consulta explícita (ex.: BGP lookup) — complementa o registo HTTP."""
    row: dict[str, Any] = {
        "event": "consultation",
        "user_id": user_id,
        "username": username,
        "role": role,
        "consultation": consultation,
        "client_ip": client_ip,
    }
    if device_id is not None:
        row["device_id"] = device_id
    if detail:
        row["detail"] = detail
    write_audit_record(row)
