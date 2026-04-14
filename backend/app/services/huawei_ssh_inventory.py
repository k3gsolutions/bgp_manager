"""
Coleta inventário via SSH (comandos VRP Huawei) — alinhado a netops_netbox_sync.
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from ..activity_log import emit
from ..crypto import decrypt
from ..huawei_cli.adapter import build_inventory_payload_from_cli
from ..huawei_cli.collector import HuaweiNE8000Collector
from ..models import Device
from .inventory_persist import persist_inventory_payload


def _is_huawei(device: Device) -> bool:
    return (device.vendor or "").strip().lower() == "huawei"


def _collect_sync(device: Device) -> tuple[dict[str, str], dict[str, str]]:
    from netmiko import ConnectHandler

    password = decrypt(device.password_encrypted)
    conn = ConnectHandler(
        device_type="huawei_vrp",
        host=device.ip_address,
        port=device.ssh_port,
        username=device.username,
        password=password,
        timeout=180,
        auth_timeout=45,
    )
    try:
        collector = HuaweiNE8000Collector(conn)
        raw = collector.collect_all()
        vrf_bgp = collector.collect_bgp_all_vrfs(raw.get("vrfs", ""))
        return raw, vrf_bgp
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass


async def persist_huawei_cli_inventory(
    db: AsyncSession,
    device_id: int,
    device: Device,
    log: list[str],
    *,
    source: str,
    intro_message: str | None = None,
) -> dict:
    if not _is_huawei(device):
        raise ValueError("Coleta SSH VRP completa é suportada apenas para fabricante Huawei")

    if intro_message:
        emit(log, intro_message)
    else:
        emit(
            log,
            "Coleta SSH Huawei (VRP): display interface, ip, vpn-instance, bgp peer verbose, VRF BGP...",
        )

    loop = asyncio.get_running_loop()
    raw, vrf_bgp = await loop.run_in_executor(None, lambda: _collect_sync(device))
    data = build_inventory_payload_from_cli(raw, vrf_bgp, device)
    return await persist_inventory_payload(
        db,
        device_id,
        device,
        data,
        log,
        source=source,
        collect_label="SSH Huawei",
    )
