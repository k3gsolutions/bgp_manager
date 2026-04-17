from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select

from ..config import settings
from ..database import AsyncSessionLocal
from ..models import SystemUpdateHistory


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_text(existing: str, line: str) -> str:
    existing = existing or ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    existing += f"[{_now_iso()}] {line}"
    return existing


def _docker_available() -> bool:
    try:
        cp = subprocess.run(["docker", "version"], capture_output=True, text=True, timeout=10, check=False)
        return cp.returncode == 0
    except Exception:
        return False


def _docker_inspect(container_name: str) -> dict[str, Any]:
    cp = subprocess.run(
        ["docker", "inspect", container_name],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or f"docker inspect falhou para container={container_name!r}")
    data = json.loads(cp.stdout)
    if not data or not isinstance(data, list):
        raise RuntimeError("docker inspect retornou JSON inesperado")
    return data[0]


def _docker_run_with_container_config(
    *,
    container_name: str,
    image: str,
    inspect_obj: dict[str, Any],
) -> None:
    env_list: list[str] = inspect_obj.get("Config", {}).get("Env") or []
    cmd_list: list[str] | None = inspect_obj.get("Config", {}).get("Cmd")
    entrypoint_list: list[str] | None = inspect_obj.get("Config", {}).get("Entrypoint")

    host_config = inspect_obj.get("HostConfig") or {}
    restart_policy = host_config.get("RestartPolicy") or {}

    network_mode = host_config.get("NetworkMode") or ""

    mounts = inspect_obj.get("Mounts") or []
    ports = (inspect_obj.get("NetworkSettings") or {}).get("Ports") or {}

    # Monta args de docker run preservando volumes/env/ports.
    args: list[str] = ["docker", "run", "-d", "--name", container_name]

    if network_mode and network_mode != "default":
        args += ["--network", network_mode]

    # RestartPolicy: Name pode ser "unless-stopped", "always", etc.
    rp_name = restart_policy.get("Name")
    rp_max = restart_policy.get("MaximumRetryCount")
    if rp_name:
        args += ["--restart", str(rp_name)]
        if rp_max is not None:
            try:
                rp_max_i = int(rp_max)
            except Exception:
                rp_max_i = None
            if rp_max_i is not None:
                # Docker usa só MaximumRetryCount como parte do --restart? Em geral, ignoramos.
                pass

    for e in env_list:
        # env é do tipo ["KEY=VALUE", ...]
        if not isinstance(e, str) or "=" not in e:
            continue
        args += ["-e", e]

    # Port mapping: NetworkSettings.Ports tem chaves "8000/tcp" e valor lista de bindings.
    for container_port_proto, bindings in ports.items():
        if not bindings:
            continue
        # Ex.: "8000/tcp"
        try:
            container_port = container_port_proto.split("/")[0]
        except Exception:
            continue
        for b in bindings:
            host_ip = b.get("HostIp") or "0.0.0.0"
            host_port = b.get("HostPort")
            if not host_port:
                continue
            args += ["-p", f"{host_ip}:{host_port}:{container_port}"]
            break

    for m in mounts:
        mtype = m.get("Type")
        if mtype not in {"volume", "bind"}:
            continue
        src = m.get("Source")
        dst = m.get("Destination")
        if not src or not dst:
            continue
        rw = m.get("RW", True)
        mode = ":ro" if rw is False else ""
        args += ["-v", f"{src}:{dst}{mode}"]

    # Preserva entrada e cmd, se existirem.
    if entrypoint_list:
        # `--entrypoint` precisa vir antes da imagem na linha de comando.
        # entrypoint costuma ser array; usamos o primeiro item como string.
        args += ["--entrypoint", entrypoint_list[0]]

    args.append(image)

    if cmd_list:
        args += list(cmd_list)

    cp = subprocess.run(args, capture_output=True, text=True, timeout=120, check=False)
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or "docker run falhou")


async def _wait_health(url: str, timeout_seconds: int) -> None:
    start = datetime.now(timezone.utc)
    async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
        while True:
            try:
                r = await client.get(url)
                # Em geral retornamos JSON: {"status":"ok"}; aceitamos 2xx como health ok.
                if 200 <= r.status_code < 300:
                    try:
                        j = r.json()
                        if isinstance(j, dict) and (j.get("status") in {"ok", "healthy"} or "status" not in j):
                            return
                    except Exception:
                        return
            except Exception:
                pass

            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            if elapsed > timeout_seconds:
                raise RuntimeError(f"health-check falhou após {timeout_seconds}s: {url}")
            await asyncio.sleep(2.0)


async def _mark_history(session, history_id: int, *, status: str, log_line: str | None = None, finish: bool) -> None:
    row = await session.execute(select(SystemUpdateHistory).where(SystemUpdateHistory.id == history_id))
    h = row.scalar_one_or_none()
    if not h:
        return
    if log_line:
        h.log_text = _append_text(h.log_text, log_line)
    h.status = status
    if finish:
        h.finished_at = datetime.now(timezone.utc)
    await session.commit()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history-id", type=int, required=True)
    args = parser.parse_args()

    history_id = args.history_id

    # Abre conexão DB (modo async).
    async with AsyncSessionLocal() as session:
        hq = await session.execute(select(SystemUpdateHistory).where(SystemUpdateHistory.id == history_id))
        h = hq.scalar_one_or_none()
        if not h:
            return 2

        await _mark_history(session, history_id, status=h.status, log_line="Updater iniciando...", finish=False)

        container_name = (settings.system_update_container_name or "").strip()
        if not container_name:
            await _mark_history(
                session,
                history_id,
                status="failed",
                log_line="system_update_container_name não configurado; cannot aplicar update.",
                finish=True,
            )
            return 1

        if not _docker_available():
            await _mark_history(
                session,
                history_id,
                status="failed",
                log_line="Docker não disponível (docker CLI falhou).",
                finish=True,
            )
            return 1

        ghcr_repo = settings.system_update_ghcr_image_repo.strip()
        if not ghcr_repo:
            await _mark_history(session, history_id, status="failed", log_line="system_update_ghcr_image_repo vazio.", finish=True)
            return 1

        from_version = (h.from_version or "").strip()
        to_version = (h.to_version or "").strip()

        # Segurança: versões devem ser semânticas com prefixo opcional `v`.
        # (Classificação semver já ocorre no backend; aqui validamos formato mínimo.)
        def _looks_like_semver_tag(v: str) -> bool:
            v = (v or "").strip()
            if v.startswith((" ", "\t")):
                return False
            if v.startswith("v"):
                v = v[1:]
            parts = v.split(".")
            if len(parts) != 3:
                return False
            return all(p.isdigit() for p in parts)

        if not _looks_like_semver_tag(from_version) or not _looks_like_semver_tag(to_version):
            await _mark_history(session, history_id, status="failed", log_line="from/to_version não parecem semver válido.", finish=True)
            return 1

        # Normaliza para formato usado na imagem: ghcr repo + ":" + "vX.Y.Z"
        def _ensure_v(v: str) -> str:
            v = (v or "").strip()
            return v if v.startswith("v") else f"v{v}"

        from_tag = _ensure_v(from_version)
        to_tag = _ensure_v(to_version)

        previous_image = f"{ghcr_repo}:{from_tag}"
        target_image = f"{ghcr_repo}:{to_tag}"

        await _mark_history(session, history_id, status=h.status, log_line=f"Container={container_name} image atual esperada={previous_image}", finish=False)

        # Inspeciona container atual (antes de substituir).
        try:
            inspect_obj = _docker_inspect(container_name)
        except Exception as e:
            await _mark_history(session, history_id, status="failed", log_line=f"Falha ao inspecionar container: {e!s}", finish=True)
            return 1

        current_image = (inspect_obj.get("Config") or {}).get("Image") or ""
        if ghcr_repo and current_image and not (
            current_image.startswith(ghcr_repo + ":") or current_image.startswith(ghcr_repo + "@")
        ):
            await _mark_history(
                session,
                history_id,
                status="failed",
                log_line=f"Imagem atual do container não parece ser a esperada (current_image={current_image}).",
                finish=True,
            )
            return 1

        # Sempre garante rollback se health falhar.
        action_mode = (h.mode or "").strip().lower()
        if action_mode == "rollback":
            # Em rollback, `to_version` é a versão alvo anterior.
            desired_image = f"{ghcr_repo}:{to_tag}"
            await _mark_history(session, history_id, status="in_progress", log_line=f"Rollback: target image={desired_image}", finish=False)
        else:
            desired_image = target_image

        await _mark_history(session, history_id, status="in_progress", log_line=f"Pull image: {desired_image}", finish=False)
        cp_pull = subprocess.run(["docker", "pull", desired_image], capture_output=True, text=True, timeout=600, check=False)
        if cp_pull.returncode != 0:
            await _mark_history(session, history_id, status="failed", log_line=f"docker pull falhou: {cp_pull.stderr.strip()}", finish=True)
            return 1

        # Para substituir o container, removemos o atual e recriamos com a mesma config.
        try:
            subprocess.run(["docker", "stop", container_name], capture_output=True, text=True, timeout=60, check=False)
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, text=True, timeout=60, check=False)
        except Exception:
            # Mesmo que falhe, tente; docker vai reclamar se não existir.
            pass

        # Recria container com target image, preservando volumes/env/ports.
        try:
            _docker_run_with_container_config(container_name=container_name, image=desired_image, inspect_obj=inspect_obj)
        except Exception as e:
            await _mark_history(session, history_id, status="failed", log_line=f"Falha ao recriar container: {e!s}", finish=True)
            return 1

        # Descobre host port para o /health chamar.
        internal_port = int(getattr(settings, "system_update_app_internal_port", settings.app_port))
        health_path = (settings.system_update_health_path or "/health").strip() or "/health"
        timeout_seconds = int(settings.system_update_health_timeout_seconds)

        host_port = None
        ports = (inspect_obj.get("NetworkSettings") or {}).get("Ports") or {}
        key = f"{internal_port}/tcp"
        if key in ports and ports[key]:
            host_port = ports[key][0].get("HostPort")
        if not host_port:
            # Fallback: pega a primeira porta mapeada.
            for k, bindings in ports.items():
                if bindings:
                    host_port = bindings[0].get("HostPort")
                    break

        if not host_port:
            await _mark_history(
                session,
                history_id,
                status="failed",
                log_line="Não foi possível determinar host port via docker inspect; abortando com segurança.",
                finish=True,
            )
            return 1

        url = f"http://127.0.0.1:{int(host_port)}{health_path}"
        await _mark_history(session, history_id, status="in_progress", log_line=f"health-check: {url}", finish=False)

        try:
            await _wait_health(url, timeout_seconds=timeout_seconds)
            await _mark_history(session, history_id, status="success" if action_mode != "rollback" else "rolled_back", log_line="Health OK.", finish=True)
            return 0
        except Exception as e:
            # health falhou: rollback obrigatório para apply; em rollback, marca failed.
            if action_mode == "rollback":
                await _mark_history(session, history_id, status="failed", log_line=f"Rollback health falhou: {e!s}", finish=True)
                return 1

            await _mark_history(session, history_id, status="failed", log_line=f"Health falhou; iniciando rollback automático. Detalhe: {e!s}", finish=False)

            # Recria container com a imagem anterior e tenta health novamente.
            try:
                subprocess.run(["docker", "stop", container_name], capture_output=True, text=True, timeout=60, check=False)
                subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, text=True, timeout=60, check=False)
            except Exception:
                pass

            try:
                _docker_run_with_container_config(container_name=container_name, image=previous_image, inspect_obj=inspect_obj)
            except Exception as e2:
                await _mark_history(session, history_id, status="failed", log_line=f"Rollback falhou ao recriar container: {e2!s}", finish=True)
                return 1

            # Retry health.
            try:
                await _wait_health(url, timeout_seconds=timeout_seconds)
                await _mark_history(session, history_id, status="rolled_back", log_line="Rollback OK (health recuperado).", finish=True)
                return 0
            except Exception as e3:
                await _mark_history(session, history_id, status="failed", log_line=f"Rollback falhou (health novamente falhou): {e3!s}", finish=True)
                return 1


if __name__ == "__main__":
    # Rodar como `python -m app.updater.system_update_worker --history-id N`
    raise SystemExit(asyncio.run(main()))

