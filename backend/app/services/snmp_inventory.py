"""
Coleta SNMP completa e delega persistência a `inventory_persist`.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
from sqlalchemy.ext.asyncio import AsyncSession

from ..activity_log import emit
from ..crypto import decrypt
from ..models import Device
from ..snmp_collector import async_collect_all
from ..huawei_cli.parsers_bgp import parse_bgp_peers_verbose
from .interface_name import canonical_interface_name
from .config_snapshot import persist_running_config_snapshot, running_config_fetch_needed
from .inventory_persist import _bgp_peer_row_key, is_ibgp_session, persist_inventory_payload

__all__ = ["is_ibgp_session", "persist_snmp_inventory"]


def _is_huawei(device: Device) -> bool:
    return (device.vendor or "").strip().lower() == "huawei"


def _parse_ipv6_tokens(text: str) -> list[str]:
    out: list[str] = []
    for raw in re.split(r"\s+", text.strip()):
        token = raw.strip(",;()[]")
        if ":" not in token:
            continue
        try:
            if "/" in token:
                out.append(str(ipaddress.ip_interface(token)))
            else:
                out.append(str(ipaddress.ip_address(token)))
        except ValueError:
            continue
    dedup: list[str] = []
    seen = set()
    for item in out:
        if item not in seen:
            dedup.append(item)
            seen.add(item)
    return dedup


def _collect_huawei_ipv6_map_sync(
    device: Device,
    fetch_running_config: bool,
) -> tuple[dict[str, list[str]], str | None]:
    """
    Fallback leve: IPv6 por SSH quando SNMP não retorna endereços IPv6.
    Só executa ``display current-configuration`` quando ``fetch_running_config`` é True
    (janela horária desde o último snapshot — ver ``running_config_fetch_needed``).
    """
    from netmiko import ConnectHandler

    password = decrypt(device.password_encrypted)
    conn = ConnectHandler(
        device_type="huawei_vrp",
        host=device.ip_address,
        port=device.ssh_port,
        username=device.username,
        password=password,
        timeout=120,
        auth_timeout=45,
        fast_cli=False,
    )
    snap: str | None = None
    out = ""
    try:
        if fetch_running_config:
            snap = (conn.send_command("display current-configuration", read_timeout=600) or "").strip() or None
        out = conn.send_command("display ipv6 interface brief", read_timeout=120) or ""
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass

    by_iface: dict[str, list[str]] = {}
    current_iface: str | None = None
    for raw_line in out.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("Interface", "-", "IPv6", "Address")):
            continue

        first = line.split()[0]
        if re.match(r"^[A-Za-z][A-Za-z0-9\-]*(?:\d+)(?:/\d+)*(?:\.\d+)?$", first):
            current_iface = first
            by_iface.setdefault(current_iface, [])

        ips = _parse_ipv6_tokens(line)
        if ips and current_iface:
            cur = by_iface.setdefault(current_iface, [])
            for ip6 in ips:
                if ip6 not in cur:
                    cur.append(ip6)
    return by_iface, snap


async def _enrich_ipv6_via_huawei_ssh_if_needed(
    device: Device,
    data: dict,
    log: list[str],
    db: AsyncSession,
    device_id: int,
) -> str:
    interfaces = data.get("interfaces") or []
    if not interfaces:
        return "none"

    snmp_ipv6_count = sum(len(i.get("ipv6_addresses") or []) for i in interfaces)
    if snmp_ipv6_count > 0:
        emit(log, f"IPv6 via SNMP: {snmp_ipv6_count} endereço(s) coletado(s).")
        return "snmp"

    if not _is_huawei(device):
        emit(log, "IPv6 via SNMP: nenhum endereço retornado; fallback SSH indisponível para este vendor.")
        return "none"
    if not device.username or not device.password_encrypted:
        emit(log, "IPv6 via SNMP: nenhum endereço retornado; sem credenciais SSH para fallback.")
        return "none"

    emit(log, "IPv6 via SNMP: nenhum endereço retornado; tentando fallback SSH Huawei...")
    fetch_cfg = await running_config_fetch_needed(db, device_id)
    if not fetch_cfg:
        emit(
            log,
            "Running-config: último snapshot dentro da janela horária — "
            "omitindo ``display current-configuration`` na sessão IPv6 SSH.",
        )
    try:
        ipv6_map, running_cfg = await asyncio.to_thread(
            _collect_huawei_ipv6_map_sync,
            device,
            fetch_cfg,
        )
    except Exception as e:
        emit(log, f"Fallback SSH de IPv6 falhou: {e!s}")
        return "none"

    if running_cfg:
        try:
            await persist_running_config_snapshot(
                db, device_id, device, log, running_cfg, source="ssh_ipv6_fallback"
            )
        except Exception as e:
            emit(log, f"Snapshot running-config (IPv6 SSH) não gravado: {e!s}")

    if not ipv6_map:
        emit(log, "Fallback SSH de IPv6 executado, mas sem endereços IPv6 retornados.")
        return "none"

    iface_by_name = {
        canonical_interface_name(i.get("name")).lower(): i
        for i in interfaces
        if canonical_interface_name(i.get("name"))
    }
    applied = 0
    for ifname, addrs in ipv6_map.items():
        row = iface_by_name.get(canonical_interface_name(ifname).lower())
        if not row:
            continue
        merged = list(dict.fromkeys((row.get("ipv6_addresses") or []) + addrs))
        if merged != (row.get("ipv6_addresses") or []):
            row["ipv6_addresses"] = merged
            applied += len(addrs)

    total_after = sum(len(i.get("ipv6_addresses") or []) for i in interfaces)
    emit(
        log,
        f"Fallback SSH IPv6 aplicado: {applied} endereço(s) adicionados; total IPv6 no inventário={total_after}.",
    )
    return "ssh_fallback" if total_after > 0 else "none"


def _collect_huawei_bgp_verbose_peers_sync(
    device: Device,
    fetch_running_config: bool,
) -> tuple[list[dict], str | None]:
    """
    Peers BGP (IPv4/IPv6) via SSH verbose.
    ``display current-configuration`` só quando ``fetch_running_config`` (janela horária).
    """
    from netmiko import ConnectHandler

    password = decrypt(device.password_encrypted)
    conn = ConnectHandler(
        device_type="huawei_vrp",
        host=device.ip_address,
        port=device.ssh_port,
        username=device.username,
        password=password,
        timeout=120,
        auth_timeout=45,
        fast_cli=False,
    )
    snap: str | None = None
    out_v4 = ""
    out_v6 = ""
    try:
        if fetch_running_config:
            snap = (conn.send_command("display current-configuration", read_timeout=600) or "").strip() or None
        out_v4 = conn.send_command("display bgp peer verbose", read_timeout=180) or ""
        out_v6 = conn.send_command("display bgp ipv6 peer verbose", read_timeout=180) or ""
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass

    parsed = parse_bgp_peers_verbose(out_v4 or "") + parse_bgp_peers_verbose(out_v6 or "")
    peers: list[dict] = []
    for p in parsed:
        pip = p.get("peer_ip")
        if not pip:
            continue
        rpi = (p.get("route_policy_import") or "").strip()[:512] or None
        rpe = (p.get("route_policy_export") or "").strip()[:512] or None
        peers.append(
            {
                "peer_ip": pip,
                "remote_as": p.get("peer_as"),
                "local_addr": None,
                "state": (p.get("state") or "unknown").strip().lower(),
                "vrf_name": (p.get("vrf_name") or "").strip()[:128],
                # in_updates -> rotas recebidas | out_updates -> rotas anunciadas
                "in_updates": p.get("received_total_routes"),
                "out_updates": p.get("advertised_total_routes"),
                "uptime_secs": None,
                "route_policy_import": rpi,
                "route_policy_export": rpe,
            }
        )
    return peers, snap


async def _enrich_bgp_via_huawei_ssh_if_needed(
    device: Device,
    data: dict,
    log: list[str],
    db: AsyncSession,
    device_id: int,
) -> str:
    bgp = data.get("bgp") or {}
    peers = bgp.get("peers") or []
    snmp_ipv6_count = sum(1 for p in peers if ":" in str(p.get("peer_ip", "")))
    had_ipv6_from_snmp = snmp_ipv6_count > 0
    if had_ipv6_from_snmp:
        emit(log, f"Peers BGP IPv6 via SNMP: {snmp_ipv6_count} coletado(s).")

    if not _is_huawei(device):
        if not had_ipv6_from_snmp:
            emit(log, "Peers BGP IPv6: SNMP sem IPv6 e fallback SSH indisponível para este vendor.")
        return "none"
    if not device.username or not device.password_encrypted:
        emit(log, "Peers BGP IPv6: SNMP sem IPv6 e sem credenciais SSH para fallback.")
        return "none"

    if had_ipv6_from_snmp:
        emit(log, "Peers BGP: enriquecendo contadores de rotas via SSH verbose...")
    else:
        emit(log, "Peers BGP IPv6: SNMP sem IPv6, tentando fallback SSH Huawei...")
    fetch_cfg = await running_config_fetch_needed(db, device_id)
    if not fetch_cfg:
        emit(
            log,
            "Running-config: último snapshot dentro da janela horária — "
            "omitindo ``display current-configuration`` na sessão BGP verbose SSH.",
        )
    try:
        ssh_peers, running_cfg = await asyncio.to_thread(
            _collect_huawei_bgp_verbose_peers_sync,
            device,
            fetch_cfg,
        )
    except Exception as e:
        emit(log, f"Enriquecimento SSH de peers BGP falhou: {e!s}")
        return "none"

    if running_cfg:
        try:
            await persist_running_config_snapshot(
                db, device_id, device, log, running_cfg, source="ssh_bgp_verbose"
            )
        except Exception as e:
            emit(log, f"Snapshot running-config (BGP SSH) não gravado: {e!s}")

    if not ssh_peers:
        emit(log, "Enriquecimento SSH de peers BGP executado, mas sem dados retornados.")
        return "none"

    by_key: dict[tuple[str, str], dict] = {}
    for p in peers:
        k = _bgp_peer_row_key(p)
        if k[0]:
            by_key[k] = p
    added = 0
    enriched = 0
    for ssh_peer in ssh_peers:
        pip = str(ssh_peer.get("peer_ip") or "")
        if not pip:
            continue
        sk = _bgp_peer_row_key(ssh_peer)
        if sk in by_key:
            p = by_key[sk]
            if ssh_peer.get("in_updates") is not None:
                p["in_updates"] = ssh_peer.get("in_updates")
            if ssh_peer.get("out_updates") is not None:
                p["out_updates"] = ssh_peer.get("out_updates")
            if ssh_peer.get("route_policy_import"):
                p["route_policy_import"] = ssh_peer.get("route_policy_import")
            if ssh_peer.get("route_policy_export"):
                p["route_policy_export"] = ssh_peer.get("route_policy_export")
            if p.get("state") in (None, "", "unknown") and ssh_peer.get("state"):
                p["state"] = ssh_peer.get("state")
            enriched += 1
            continue
        peers.append(ssh_peer)
        by_key[sk] = ssh_peer
        added += 1
    bgp["peers"] = peers
    data["bgp"] = bgp
    emit(log, f"Peers BGP via SSH: {enriched} peer(s) enriquecido(s), {added} peer(s) adicionado(s).")
    if had_ipv6_from_snmp:
        return "ssh_enriched"
    return "ssh_fallback" if added > 0 else "none"


async def persist_snmp_inventory(
    db: AsyncSession,
    device_id: int,
    device: Device,
    log: list[str],
    *,
    intro_message: str | None = None,
    source: str = "snmp_collect",
) -> dict:
    if not device.snmp_community:
        raise ValueError("Community SNMP não configurada")

    if intro_message:
        emit(log, intro_message)
    else:
        emit(log, "Executando SNMP (sistema, interfaces, BGP4-MIB, VRFs)...")

    data = await async_collect_all(device.ip_address, device.snmp_community)
    ipv6_source = await _enrich_ipv6_via_huawei_ssh_if_needed(device, data, log, db, device_id)
    bgp_ipv6_source = await _enrich_bgp_via_huawei_ssh_if_needed(device, data, log, db, device_id)
    data["ipv6_source"] = ipv6_source
    data["bgp_ipv6_source"] = bgp_ipv6_source
    return await persist_inventory_payload(
        db,
        device_id,
        device,
        data,
        log,
        source=source,
        collect_label="SNMP",
    )
