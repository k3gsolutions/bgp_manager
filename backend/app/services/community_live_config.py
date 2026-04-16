"""Obter ``display current-configuration`` via SSH (Huawei) para resync live de communities."""

from __future__ import annotations

import asyncio
import socket
import time
from typing import Any

from ..crypto import decrypt
from ..models import Device


def _vendor_to_netmiko(vendor: str) -> str:
    v = (vendor or "").strip().lower()
    if "huawei" in v or "vrp" in v:
        return "huawei"
    return "huawei"


def _fetch_display_current_configuration(device_params: dict[str, Any]) -> str:
    from netmiko import ConnectHandler

    try:
        with socket.create_connection((device_params["host"], int(device_params["port"])), timeout=8):
            pass
    except OSError as tcp_e:
        raise RuntimeError(f"TCP falhou ({tcp_e!s})") from tcp_e

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
    try:
        out = (
            conn.send_command_timing(
                "display current-configuration",
                read_timeout=180,
                strip_prompt=False,
                strip_command=False,
            )
            or ""
        )
        return out
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass


async def fetch_current_configuration_via_ssh(device: Device) -> str:
    password = decrypt(device.password_encrypted)
    device_params: dict[str, Any] = {
        "device_type": _vendor_to_netmiko(device.vendor),
        "host": device.ip_address,
        "port": device.ssh_port,
        "username": device.username,
        "password": password,
        "timeout": 180,
        "conn_timeout": 20,
        "banner_timeout": 45,
        "auth_timeout": 30,
        "fast_cli": False,
    }
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _fetch_display_current_configuration(device_params))
