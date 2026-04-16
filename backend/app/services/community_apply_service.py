"""Preview e aplicação de community-sets (fase 1: bloco ``ip community-list``) via SSH."""

from __future__ import annotations

import asyncio
import hashlib
import re
import socket
import time
import unicodedata
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..crypto import decrypt
from ..models import CommunityChangeAudit, CommunityLibraryItem, CommunitySet, CommunitySetMember, Device
from .community_sync_service import IMPORTED_COMMUNITY_SET_ORIGINS, latest_running_config_text
from .config_snapshot import persist_running_config_snapshot
from .huawei_community_parser import community_list_names_in_config, format_phase1_community_list_block


def slugify_display_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "")
    s = s.encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", s).strip("-").lower()
    return s[:80] or "community-set"


def validate_vrp_object_name(name: str) -> str:
    n = (name or "").strip()
    if not re.match(r"^[A-Za-z0-9_-]{1,63}$", n):
        raise ValueError(
            "vrp_object_name: use apenas letras, dígitos, hífen e underscore; comprimento 1–63."
        )
    return n


def _sha256_text(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def _vendor_to_netmiko(vendor: str) -> str:
    v = (vendor or "").strip().lower()
    if "huawei" in v or "vrp" in v:
        return "huawei"
    return "huawei"


async def _load_set_with_members(db: AsyncSession, community_set_id: int, device_id: int) -> CommunitySet | None:
    r = await db.execute(
        select(CommunitySet)
        .options(
            selectinload(CommunitySet.members).selectinload(CommunitySetMember.linked_library_item),
        )
        .where(CommunitySet.id == community_set_id, CommunitySet.device_id == device_id)
    )
    return r.scalar_one_or_none()


async def build_candidate_config_text(db: AsyncSession, s: CommunitySet) -> str:
    o = (getattr(s, "origin", None) or "app_created") or "app_created"
    if o in IMPORTED_COMMUNITY_SET_ORIGINS and not s.members:
        vals = [str(x).strip() for x in (s.discovered_members_json or []) if str(x).strip()]
        return format_phase1_community_list_block(s.vrp_object_name, vals)
    members = sorted(s.members, key=lambda m: m.position)
    values: list[str] = []
    seen: set[str] = set()
    for m in members:
        v = (m.community_value or "").strip()
        if not v:
            continue
        key = v.lower()
        if key in seen:
            continue
        seen.add(key)
        values.append(v)
    return format_phase1_community_list_block(s.vrp_object_name, values)


async def build_preview(
    db: AsyncSession,
    *,
    device: Device,
    community_set: CommunitySet,
) -> dict[str, Any]:
    candidate = await build_candidate_config_text(db, community_set)
    warnings: list[str] = []
    missing_vals = sorted(
        {(m.community_value or "").strip() for m in community_set.members if m.missing_in_library}
    )
    missing_count = sum(1 for m in community_set.members if m.missing_in_library)
    if missing_vals:
        warnings.append(
            "Existem valores neste set sem ``ip community-filter`` correspondente na biblioteca (mesmo "
            f"``community_value``): {', '.join(missing_vals)}. A aplicação está bloqueada até existirem na "
            "biblioteca ou até confirmar explicitamente o risco (segundo checkbox no modal de apply)."
        )
    cfg = await latest_running_config_text(db, device.id) or ""
    existing = community_list_names_in_config(cfg)
    if community_set.vrp_object_name in existing:
        warnings.append(
            f"Já existe ``ip community-list {community_set.vrp_object_name}`` no último running-config salvo — aplicar pode sobrepor entradas."
        )
    return {
        "candidate_config_text": candidate,
        "candidate_sha256": _sha256_text(candidate),
        "warnings": warnings,
        "members_missing_library": missing_count,
        "missing_community_values": missing_vals,
    }


async def record_audit(
    db: AsyncSession,
    *,
    device_id: int,
    community_set_id: int | None,
    user_id: int | None,
    action: str,
    candidate_config_text: str,
    command_sent_text: str | None,
    device_response_text: str | None,
    status: str,
) -> CommunityChangeAudit:
    row = CommunityChangeAudit(
        device_id=device_id,
        community_set_id=community_set_id,
        user_id=user_id,
        action=action,
        candidate_config_text=candidate_config_text or "",
        command_sent_text=command_sent_text,
        device_response_text=device_response_text,
        status=status,
    )
    db.add(row)
    await db.flush()
    return row


async def latest_successful_preview(
    db: AsyncSession, *, community_set_id: int
) -> CommunityChangeAudit | None:
    r = await db.execute(
        select(CommunityChangeAudit)
        .where(
            CommunityChangeAudit.community_set_id == community_set_id,
            CommunityChangeAudit.action == "preview",
            CommunityChangeAudit.status == "success",
        )
        .order_by(CommunityChangeAudit.id.desc())
        .limit(1)
    )
    return r.scalar_one_or_none()


def _ssh_apply_script(device_params: dict[str, Any], cmd_lines: list[str]) -> tuple[str, str]:
    """Executa comandos; devolve (log_concat, running_config_text)."""
    from netmiko import ConnectHandler

    chunks: list[str] = []
    try:
        with socket.create_connection((device_params["host"], int(device_params["port"])), timeout=5):
            pass
    except OSError as tcp_e:
        raise RuntimeError(f"Pré-check TCP falhou ({tcp_e!s})") from tcp_e

    conn = None
    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            conn = ConnectHandler(**device_params)
            break
        except Exception as e:
            last_err = e
            if attempt >= 3:
                raise RuntimeError(
                    f"SSH falhou após 3 tentativas ({device_params['host']}:{device_params['port']}): {e!s}"
                ) from e
            time.sleep(1.0 * attempt)
    assert conn is not None
    post_cfg = ""
    try:
        for line in cmd_lines:
            chunks.append(
                conn.send_command_timing(line, strip_prompt=False, strip_command=False, read_timeout=120)
            )
        post_cfg = conn.send_command_timing(
            "display current-configuration",
            read_timeout=120,
            strip_prompt=False,
            strip_command=False,
        ) or ""
        chunks.append("\n--- post-apply running-config ---\n")
        chunks.append(post_cfg)
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass
    return "".join(chunks), post_cfg


async def apply_community_set(
    db: AsyncSession,
    *,
    device: Device,
    community_set_id: int,
    user_id: int | None,
    confirm: bool,
    expected_candidate_sha256: str | None,
    acknowledge_missing_library_refs: bool = False,
) -> dict[str, Any]:
    if not confirm:
        raise ValueError("Confirmação obrigatória para aplicar no roteador.")

    s = await _load_set_with_members(db, community_set_id, device.id)
    if s is None:
        raise ValueError("Community set não encontrado.")
    if (getattr(s, "origin", None) or "app_created") in IMPORTED_COMMUNITY_SET_ORIGINS:
        raise ValueError("Sets importados do equipamento não podem ser aplicados a partir da app.")

    if any(m.missing_in_library for m in s.members) and not acknowledge_missing_library_refs:
        raise ValueError(
            "Existem valores no set sem ``community-filter`` na biblioteca. Corrija a biblioteca ou "
            "reenvie o pedido com acknowledge_missing_library_refs=true após aceitar o risco no cliente."
        )

    current = await build_candidate_config_text(db, s)
    current_hash = _sha256_text(current)

    prev = await latest_successful_preview(db, community_set_id=community_set_id)
    if prev is None:
        raise ValueError("É necessário gerar um preview com sucesso antes de aplicar.")

    if (prev.candidate_config_text or "").strip() != (current or "").strip():
        raise ValueError("O set foi alterado após o último preview. Gere um novo preview.")

    exp = (expected_candidate_sha256 or "").strip().lower()
    if not exp or exp != current_hash.lower():
        raise ValueError("Hash do candidato inválido ou desatualizado — gere um novo preview e use o SHA-256 devolvido.")

    # Monta comandos (Huawei: system-view + bloco + quit + commit)
    block_lines = [x for x in current.splitlines() if x.strip()]
    cmd_lines = ["system-view"] + block_lines + ["quit", "commit", "quit"]
    script = "\n".join(cmd_lines)

    password = decrypt(device.password_encrypted)
    device_params: dict[str, Any] = {
        "device_type": _vendor_to_netmiko(device.vendor),
        "host": device.ip_address,
        "port": device.ssh_port,
        "username": device.username,
        "password": password,
        "timeout": 120,
        "conn_timeout": 20,
        "banner_timeout": 45,
        "auth_timeout": 30,
        "fast_cli": False,
    }

    loop = asyncio.get_running_loop()
    try:
        resp, post_cfg = await loop.run_in_executor(None, lambda: _ssh_apply_script(device_params, cmd_lines))
        apply_log: list[str] = []
        if (post_cfg or "").strip():
            await persist_running_config_snapshot(
                db,
                device_id=device.id,
                device=device,
                log=apply_log,
                config_text=post_cfg,
                source="ssh_community_set_apply",
            )
        await record_audit(
            db,
            device_id=device.id,
            community_set_id=s.id,
            user_id=user_id,
            action="apply",
            candidate_config_text=current,
            command_sent_text=script,
            device_response_text=resp[-8000:],
            status="success",
        )
        s.status = "applied"
        await db.commit()
        return {
            "ok": True,
            "status": "applied",
            "message": "Configuração aplicada e commit enviado.",
            "device_response_excerpt": resp[-2000:],
        }
    except Exception as e:
        err_txt = str(e)
        await record_audit(
            db,
            device_id=device.id,
            community_set_id=s.id,
            user_id=user_id,
            action="apply",
            candidate_config_text=current,
            command_sent_text=script,
            device_response_text=err_txt[:8000],
            status="failed",
        )
        s.status = "failed"
        await db.commit()
        raise
