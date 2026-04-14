from __future__ import annotations

import asyncio

from sqlalchemy import select

from ..activity_log import add_event
from ..crypto import decrypt
from ..database import AsyncSessionLocal
from ..models import Device
from ..snmp_collector import async_collect_status_refresh


async def _check_device_ssh(device: Device) -> None:
    try:
        from netmiko import ConnectHandler
    except Exception as e:  # pragma: no cover
        add_event("error", "STARTUP", "Netmiko indisponível para teste SSH", str(e))
        return

    params = {
        "device_type": "huawei_vrp",
        "host": device.ip_address,
        "port": device.ssh_port,
        "username": device.username,
        "password": decrypt(device.password_encrypted),
        "timeout": 40,
        "auth_timeout": 25,
        "fast_cli": False,
    }

    def _probe():
        conn = None
        try:
            conn = ConnectHandler(**params)
            conn.find_prompt()
            return True, None
        except Exception as e:
            return False, str(e)
        finally:
            if conn:
                try:
                    conn.disconnect()
                except Exception:
                    pass

    ok, err = await asyncio.to_thread(_probe)
    label = device.name or device.ip_address
    if ok:
        add_event("success", "STARTUP", f"SSH OK: {label} ({device.ip_address}:{device.ssh_port})")
    else:
        add_event("error", "STARTUP", f"SSH FALHA: {label} ({device.ip_address}:{device.ssh_port})", err)


async def _check_device_snmp(device: Device) -> None:
    label = device.name or device.ip_address
    if not device.snmp_community:
        add_event("warn", "STARTUP", f"SNMP não configurado: {label}")
        return

    try:
        await asyncio.wait_for(
            async_collect_status_refresh(device.ip_address, device.snmp_community),
            timeout=25,
        )
        add_event("success", "STARTUP", f"SNMP OK: {label}")
    except Exception as e:
        add_event("error", "STARTUP", f"SNMP FALHA: {label}", str(e))


async def run_startup_access_checks() -> None:
    add_event("info", "STARTUP", "Iniciando validação de acessibilidade dos dispositivos (SSH/SNMP)")
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Device).order_by(Device.id.asc()))
        devices = list(result.scalars().all())

    if not devices:
        add_event("info", "STARTUP", "Nenhum dispositivo cadastrado para validação inicial")
        return

    sem = asyncio.Semaphore(4)

    async def _run_for_device(device: Device) -> None:
        async with sem:
            await _check_device_ssh(device)
            await _check_device_snmp(device)

    await asyncio.gather(*[_run_for_device(d) for d in devices], return_exceptions=True)
    add_event("info", "STARTUP", f"Validação inicial concluída para {len(devices)} dispositivo(s)")
