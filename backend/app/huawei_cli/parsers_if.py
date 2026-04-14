# Adaptado de netops_netbox_sync/app/parsers/interfaces.py e ipaddr.py

from __future__ import annotations

import ipaddress
import re

IFACE_PREFIXES = (
    "100GE", "10GE", "40GE", "400GE", "25GE",
    "Eth-Trunk", "GigabitEthernet", "XGigabitEthernet",
    "LoopBack", "Loopback", "NULL", "Null",
    "Tunnel", "Virtual-Ethernet", "Virtual-Template",
    "Ethernet", "Vlanif", "Pos", "Serial",
)


def parse_interface_brief(output: str) -> list[dict]:
    interfaces: list[dict] = []
    for line in output.splitlines():
        if line != line.lstrip():
            continue
        line = line.strip()
        if not line or line.startswith("Interface") or line.startswith("-"):
            continue
        first = line.split()[0]
        if first.endswith(":") or not first.startswith(IFACE_PREFIXES):
            continue
        m = re.match(r"^(\S+)\s+(\S+)\s+(\S+)", line)
        if m:
            interfaces.append({
                "name": m.group(1),
                "admin_status": m.group(2),
                "oper_status": m.group(3).strip("()"),
            })
    return interfaces


def parse_interface_description(output: str) -> dict[str, str]:
    descriptions: dict[str, str] = {}
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("Interface") or line.startswith("-"):
            continue
        parts = re.split(r"\s{2,}", line)
        if len(parts) >= 2:
            iface = parts[0].strip()
            desc = parts[-1].strip()
            descriptions[iface] = desc
    return descriptions


def parse_ip_interface_brief(output: str) -> list[dict]:
    ips: list[dict] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("Interface") or line.startswith("-"):
            continue
        parts = re.split(r"\s{2,}", line)
        if len(parts) >= 2:
            iface = parts[0]
            ip = parts[1]
            if ip.lower() != "unassigned":
                ips.append({"interface": iface, "address": ip})
    return ips


def parse_lag_members(running_config: str) -> dict[str, str]:
    members: dict[str, str] = {}
    current_iface: str | None = None
    for line in running_config.splitlines():
        m_iface = re.match(r"^interface (\S+)", line)
        if m_iface:
            current_iface = m_iface.group(1)
            continue
        m_trunk = re.match(r"^\s+eth-trunk (\d+)$", line)
        if m_trunk and current_iface:
            members[current_iface] = f"Eth-Trunk{m_trunk.group(1)}"
    return members


def parse_ipv6_interface_brief(output: str) -> dict[str, list[str]]:
    """Extrai IPv6 por interface a partir de `display ipv6 interface brief` (best effort)."""
    by_iface: dict[str, list[str]] = {}
    current_iface: str | None = None

    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.startswith(("Interface", "-", "IPv6", "Address")):
            continue

        first = line.split()[0]
        if first.startswith(IFACE_PREFIXES):
            current_iface = first
            by_iface.setdefault(current_iface, [])

        for token in re.split(r"\s+", line):
            t = token.strip(",;()[]")
            if ":" not in t:
                continue
            try:
                val = str(ipaddress.ip_interface(t)) if "/" in t else str(ipaddress.ip_address(t))
            except ValueError:
                continue
            if current_iface:
                cur = by_iface.setdefault(current_iface, [])
                if val not in cur:
                    cur.append(val)
    return by_iface
