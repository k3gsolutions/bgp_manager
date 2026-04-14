"""Logs técnicos e de equipamento (separados da auditoria de utilizadores).

- ``logs/bgpmanager.log`` — mensagens da aplicação (logger ``bgpmanager``).
- ``logs/events.log`` — eventos de coleta / inventário (JSONL, aba Logs na UI).

Auditoria de ações por utilizador: módulo ``audit_log`` → ``logs/audit/audit.log``.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import gzip
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import shutil
from typing import MutableSequence

_logger = logging.getLogger("bgpmanager")
_event_logger = logging.getLogger("bgpmanager.events")

_LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
_APP_LOG = _LOG_DIR / "bgpmanager.log"
_EVENT_LOG = _LOG_DIR / "events.log"

_recent_events = deque(maxlen=1000)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gzip_namer(default_name: str) -> str:
    return default_name + ".gz"


def _gzip_rotator(source: str, dest: str) -> None:
    with open(source, "rb") as sf, gzip.open(dest, "wb") as df:
        shutil.copyfileobj(sf, df)
    Path(source).unlink(missing_ok=True)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": _utc_now_iso(),
            "level": (record.levelname or "INFO").lower(),
            "source": getattr(record, "source", "BACKEND"),
            "message": record.getMessage(),
            "detail": getattr(record, "detail", None),
        }
        return json.dumps(payload, ensure_ascii=False)


def add_event(level: str, source: str, message: str, detail: str | None = None) -> None:
    entry = {
        "timestamp": _utc_now_iso(),
        "level": (level or "info").lower(),
        "source": source or "BACKEND",
        "message": message,
        "detail": detail,
    }
    _recent_events.append(entry)
    log_method = getattr(_event_logger, entry["level"], _event_logger.info)
    log_method(
        entry["message"],
        extra={"source": entry["source"], "detail": entry["detail"]},
    )


def get_recent_events(limit: int = 100) -> list[dict]:
    lim = max(1, min(limit, 1000))
    events: list[dict] = list(_recent_events)
    # Fallback: se recém iniciado e deque vazio, tenta recuperar dos arquivos.
    if not events:
        events = _read_recent_events_from_files(lim * 2)
    events = sorted(events, key=lambda x: x.get("timestamp", ""), reverse=True)
    return events[:lim]


def _read_recent_events_from_files(limit: int) -> list[dict]:
    lim = max(1, min(limit, 5000))
    files = sorted(_LOG_DIR.glob("events.log*"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[dict] = []
    for fp in files:
        try:
            if fp.suffix == ".gz":
                fh = gzip.open(fp, "rt", encoding="utf-8", errors="ignore")
            else:
                fh = fp.open("r", encoding="utf-8", errors="ignore")
            with fh:
                lines = fh.readlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                out.append(obj)
                if len(out) >= lim:
                    return out
        except OSError:
            continue
    return out


def emit(log: MutableSequence[str], message: str, *, source: str = "BACKEND") -> None:
    log.append(message)
    add_event("info", source, message)
    _logger.info(message)
    print(f"[bgpmanager] {message}", flush=True)


def configure_activity_logging() -> None:
    if _logger.handlers or _event_logger.handlers:
        return

    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    # App log (texto)
    stream_h = logging.StreamHandler()
    stream_h.setFormatter(logging.Formatter("%(levelname)s [bgpmanager] %(message)s"))

    file_h = RotatingFileHandler(
        _APP_LOG,
        maxBytes=2_000_000,
        backupCount=10,
        encoding="utf-8",
    )
    file_h.namer = _gzip_namer
    file_h.rotator = _gzip_rotator
    file_h.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )

    _logger.addHandler(stream_h)
    _logger.addHandler(file_h)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False

    # Event log (jsonl), consumido pela aba de logs
    event_h = RotatingFileHandler(
        _EVENT_LOG,
        maxBytes=1_500_000,
        backupCount=20,
        encoding="utf-8",
    )
    event_h.namer = _gzip_namer
    event_h.rotator = _gzip_rotator
    event_h.setFormatter(_JsonFormatter())
    _event_logger.addHandler(event_h)
    _event_logger.setLevel(logging.INFO)
    _event_logger.propagate = False
