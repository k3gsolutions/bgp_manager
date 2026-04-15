"""
Snapshots de running-config Huawei (`display current-configuration`) no banco.

O comando **não** corre a cada sessão SSH: só quando ``running_config_fetch_needed`` é verdadeiro
(último snapshot com mais de ``config_snapshot_refresh_hours`` horas, por defeito 1).
Nessa sessão, ``display current-configuration`` corre antes dos outros ``display`` do mesmo objetivo.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..activity_log import emit
from ..config import settings
from ..models import Configuration, Device


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _refresh_delta() -> timedelta:
    h = float(getattr(settings, "config_snapshot_refresh_hours", 1.0) or 1.0)
    h = max(5 / 60.0, min(h, 168.0))  # entre 5 min e 7 dias
    return timedelta(hours=h)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def running_config_fetch_needed(db: AsyncSession, device_id: int) -> bool:
    """
    True quando deve executar ``display current-configuration`` no equipamento:
    nunca houve snapshot ou o último ``collected_at`` é anterior à janela
    ``config_snapshot_refresh_hours``.
    """
    cutoff = _now() - _refresh_delta()
    r = await db.execute(
        select(Configuration.collected_at)
        .where(Configuration.device_id == device_id)
        .order_by(Configuration.collected_at.desc())
        .limit(1)
    )
    latest_at = r.scalar_one_or_none()
    if latest_at is None:
        return True
    return _aware(latest_at) < cutoff


def _min_meaningful_config(text: str) -> bool:
    t = text.strip()
    if len(t) < 200:
        return False
    low = t.lower()
    return "version" in low or "interface" in low or "sysname" in low or "#" in t[:2000]


async def persist_running_config_snapshot(
    db: AsyncSession,
    device_id: int,
    device: Device,
    log: list[str],
    config_text: str,
    *,
    source: str,
) -> dict:
    """
    Insere linha em ``configurations`` se o texto for plausível.

    Hash igual ao último snapshot **só** impede insert se esse último ainda está dentro da
    janela ``config_snapshot_refresh_hours`` (evita duplicar na mesma hora). Passada a janela,
    grava nova linha mesmo com config idêntica (comparativo temporal).
    """
    text = (config_text or "").strip()
    if not _min_meaningful_config(text):
        emit(
            log,
            f"Snapshot running-config omitido ({device.ip_address}): saída vazia, curta ou inválida.",
        )
        return {"stored": False, "skipped": True, "reason": "invalid_or_short"}

    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    prev = await db.execute(
        select(Configuration)
        .where(Configuration.device_id == device_id)
        .order_by(Configuration.collected_at.desc())
        .limit(1)
    )
    latest = prev.scalar_one_or_none()
    cutoff = _now() - _refresh_delta()
    if latest is not None and getattr(latest, "content_sha256", None) == digest:
        la = getattr(latest, "collected_at", None)
        if la is not None and _aware(la) >= cutoff:
            emit(
                log,
                "Snapshot running-config: mesmo hash já gravado nesta janela horária — não duplicado.",
            )
            return {"stored": False, "skipped": True, "reason": "duplicate_hash_same_window"}

    bsize = len(text.encode("utf-8", errors="replace"))
    row = Configuration(
        device_id=device_id,
        config_text=text,
        collected_at=_now(),
        version="huawei_vrp",
        source=(source or "ssh")[:40],
        content_sha256=digest,
        byte_size=bsize,
    )
    db.add(row)
    await db.flush()

    limit = max(1, min(int(getattr(settings, "config_snapshot_retention", 30)), 500))
    res = await db.execute(
        select(Configuration.id)
        .where(Configuration.device_id == device_id)
        .order_by(Configuration.collected_at.desc())
    )
    ids = [r[0] for r in res.all()]
    excess = ids[limit:]
    if excess:
        await db.execute(delete(Configuration).where(Configuration.id.in_(excess)))
        emit(log, f"Retenção de snapshots: removido(s) {len(excess)} registro(s) antigo(s) (máx. {limit}).")

    emit(
        log,
        f"Snapshot running-config gravado (origem={source!r}, id={row.id}, {bsize} bytes).",
    )
    return {
        "stored": True,
        "skipped": False,
        "id": row.id,
        "byte_size": bsize,
        "content_sha256": digest,
    }
