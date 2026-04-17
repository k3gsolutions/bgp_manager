from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select

from ..config import settings
from ..models import SystemUpdateHistory


_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


app_env = settings.app_env or ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_tag(tag: str) -> str:
    t = (tag or "").strip()
    if not t:
        return t
    return t if t.startswith("v") else f"v{t}"


def parse_semver(tag: str) -> tuple[int, int, int] | None:
    m = _SEMVER_RE.match((tag or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def semver_update_type(current: str, latest: str) -> str:
    """Retorna patch | minor | major | none."""
    pc = parse_semver(current)
    pl = parse_semver(latest)
    if pc is None or pl is None:
        return "none"
    if pc == pl:
        return "none"
    if pl[0] != pc[0]:
        return "major"
    if pl[1] != pc[1]:
        return "minor"
    return "patch"


def _release_notes_summary(release: dict[str, Any], max_len: int = 600) -> str | None:
    body = (release.get("body") or "").strip()
    if not body:
        name = (release.get("name") or "").strip()
        return name or None
    # Não tente renderizar Markdown aqui; só resume por tamanho.
    if len(body) <= max_len:
        return body
    return body[: max_len - 3].rstrip() + "..."


async def _github_latest_release(owner: str, repo: str, token: str | None) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "bgp-manager-system-updater",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    timeout = httpx.Timeout(15.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url, headers=headers)
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail="GitHub não encontrou releases/latest para o repositório.")
        if r.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Falha ao consultar GitHub: {r.status_code} {r.text[:200]}")
        return r.json()


@dataclass
class _InMemoryCheckState:
    last_checked_at: str | None = None
    latest_version: str | None = None
    update_available: bool = False
    update_type: str = "none"
    latest_release_notes_summary: str | None = None
    latest_tag_source: str | None = None


_state = _InMemoryCheckState()
_lock = threading.Lock()


def get_local_version() -> str:
    return _normalize_tag(settings.app_version)


async def check_update(db, user_id: int) -> dict[str, Any]:
    """Consulta GitHub e classifica update (sem aplicar)."""
    current_tag = get_local_version()
    current_sem = parse_semver(current_tag)
    if current_sem is None:
        raise HTTPException(status_code=500, detail=f"APP_VERSION não parece semver válido: {settings.app_version!r}")

    owner = (settings.system_update_github_owner or "").strip()
    repo = (settings.system_update_github_repo or "").strip()
    if not owner or not repo:
        raise HTTPException(status_code=500, detail="Configurar system_update_github_owner/system_update_github_repo.")

    token = (settings.system_update_github_token or "").strip() if hasattr(settings, "system_update_github_token") else ""
    token = token or None

    release: dict[str, Any]
    try:
        release = await _github_latest_release(owner=owner, repo=repo, token=token)
    except HTTPException:
        raise
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=502, detail=f"Falha ao consultar GitHub: {e!s}") from e

    tag = (release.get("tag_name") or "").strip()
    if not tag:
        raise HTTPException(status_code=502, detail="GitHub release sem tag_name.")

    latest_tag = _normalize_tag(tag)
    if parse_semver(latest_tag) is None:
        raise HTTPException(status_code=502, detail=f"Tag do GitHub não parece semver: {tag!r}")

    utype = semver_update_type(current_tag, latest_tag)
    available = utype != "none"

    summary = _release_notes_summary(release)
    last_checked = _now_iso()
    with _lock:
        _state.last_checked_at = last_checked
        _state.latest_version = latest_tag
        _state.update_available = available
        _state.update_type = utype
        _state.latest_release_notes_summary = summary
        _state.latest_tag_source = "release/latest"

    status_str = "update_available" if available else "up_to_date"
    return {
        "update_available": available,
        "update_type": ("patch" if utype == "patch" else "minor" if utype == "minor" else "major" if utype == "major" else "none"),
        "current_version": current_tag,
        "latest_version": latest_tag if available else current_tag,
        "latest_release_notes_summary": summary,
        "latest_tag_source": "release/latest",
        "last_check_id": None,
        "status": status_str,
        "last_checked_at": last_checked,
    }


async def _ensure_no_in_progress_update(db) -> None:
    q = await db.execute(select(SystemUpdateHistory).where(SystemUpdateHistory.status == "in_progress"))
    row = q.scalar_one_or_none()
    if row:
        raise HTTPException(status_code=409, detail=f"Já existe update em progresso (history_id={row.id}).")


async def apply_update(
    *,
    db,
    user_id: int,
    mode: str,
    confirm: bool,
    confirm_strong: bool,
    target_version: str | None,
) -> dict[str, Any]:
    await _ensure_no_in_progress_update(db)

    # Reconsulta para evitar mismatch.
    check = await check_update(db=db, user_id=user_id)
    if not check["update_available"]:
        raise HTTPException(status_code=409, detail="Sem atualização disponível.")

    utype = check["update_type"]
    latest_tag = check["latest_version"]
    current_tag = check["current_version"]

    if target_version:
        tv = _normalize_tag(target_version)
        if tv != latest_tag:
            raise HTTPException(status_code=422, detail=f"target_version não corresponde à latest_version (target={tv}, latest={latest_tag}).")

    req_mode = (mode or "manual").strip().lower()
    if req_mode not in {"manual", "auto_patch"}:
        raise HTTPException(status_code=422, detail="mode inválido; use 'manual' ou 'auto_patch'.")

    # Regra de confirmação (segurança).
    # patch: pode ser automático opcionalmente; aqui `auto_patch` depende do settings.
    if utype == "patch":
        if req_mode == "auto_patch":
            if not settings.system_update_auto_patch:
                raise HTTPException(status_code=422, detail="Auto patch desabilitado na configuração.")
        else:
            if not confirm:
                raise HTTPException(status_code=422, detail="Para patch manual, confirme 'confirm=true'.")
    elif utype == "minor":
        if not confirm:
            raise HTTPException(status_code=422, detail="Minor exige confirmação manual (confirm=true).")
        if req_mode != "manual":
            raise HTTPException(status_code=422, detail="Minor não pode ser auto_patch.")
    elif utype == "major":
        if not (confirm and confirm_strong):
            raise HTTPException(status_code=422, detail="Major exige confirmação forte (confirm=true e confirm_strong=true).")
        if req_mode != "manual":
            raise HTTPException(status_code=422, detail="Major não pode ser auto_patch.")
    else:
        raise HTTPException(status_code=409, detail="Não foi possível classificar update (utype inesperado).")

    # Cria histórico e dispara updater separado.
    st_mode = "auto_patch" if req_mode == "auto_patch" else "manual"
    entry = SystemUpdateHistory(
        from_version=current_tag,
        to_version=latest_tag,
        update_type=utype,
        triggered_by=user_id,
        mode=st_mode,
        status="in_progress",
        log_text="",
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)

    # Dispara process separado (updater separado; sem auto-update no processo do FastAPI).
    # Trabalha via módulo python para manter o mesmo ambiente/paths.
    history_id = entry.id
    worker_module = "app.updater.system_update_worker"
    # Base de execução: backend/ (para que `app` seja resolvível).
    worker_cwd = __import__("pathlib").Path(__file__).resolve().parents[2]
    cmd = [
        sys.executable,
        "-m",
        worker_module,
        "--history-id",
        str(history_id),
    ]
    env = dict(**__import__("os").environ)
    # Pequeno hint para logs; o worker lê pelo DB como fonte da verdade.
    env["BGP_MANAGER_SYSTEM_UPDATE_HISTORY_ID"] = str(history_id)
    subprocess.Popen(cmd, cwd=str(worker_cwd), env=env)  # nosec B603 (intencional)

    return {"history_id": history_id, "status": "running"}


async def rollback_update(
    *,
    db,
    user_id: int,
    confirm: bool,
    confirm_strong: bool,
    history_id: int | None,
) -> dict[str, Any]:
    await _ensure_no_in_progress_update(db)

    if not confirm:
        raise HTTPException(status_code=422, detail="Rollback exige confirm=true.")
    if not confirm_strong:
        raise HTTPException(status_code=422, detail="Rollback exige confirm_strong=true (forte).")

    # Escolhe histórico alvo: preferir explícito; caso não, pega o último com status=failed.
    target = None
    if history_id is not None:
        q = await db.execute(select(SystemUpdateHistory).where(SystemUpdateHistory.id == history_id))
        target = q.scalar_one_or_none()
        if not target:
            raise HTTPException(status_code=404, detail="history_id não encontrado.")
    else:
        q = await db.execute(
            select(SystemUpdateHistory)
            .where(SystemUpdateHistory.status == "failed")
            .order_by(SystemUpdateHistory.created_at.desc())
            .limit(1)
        )
        target = q.scalar_one_or_none()

    if not target:
        raise HTTPException(status_code=409, detail="Não há histórico failed para rollback.")

    # rollback: from_version= atual (from_version do failed), to_version= versão anterior (que está em target.from_version?).
    # No nosso modelo, ao aplicar um update: from_version=versão atual antes; to_version=versão nova.
    # Se falhou, o rollback deve voltar para `from_version`.
    # Para simplificar: usar o mesmo container, trocando para a versão `target.from_version`.
    rollback_from = target.to_version  # o container pode ter sido parcialmente alterado; refletimos pelo que aplicamos
    rollback_to = target.from_version  # versão anterior confirmada

    # Create history row for rollback action.
    entry = SystemUpdateHistory(
        from_version=rollback_from,
        to_version=rollback_to,
        update_type=target.update_type,
        triggered_by=user_id,
        mode="rollback",
        status="in_progress",
        log_text="",
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)

    worker_module = "app.updater.system_update_worker"
    worker_cwd = __import__("pathlib").Path(__file__).resolve().parents[2]
    cmd = [
        sys.executable,
        "-m",
        worker_module,
        "--history-id",
        str(entry.id),
    ]
    env = dict(**__import__("os").environ)
    env["BGP_MANAGER_SYSTEM_UPDATE_HISTORY_ID"] = str(entry.id)
    subprocess.Popen(cmd, cwd=str(worker_cwd), env=env)  # nosec B603 (intencional)
    return {"history_id": entry.id, "status": "running"}

