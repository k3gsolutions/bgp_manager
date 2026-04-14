"""Perfis e permissões RBAC (fonte da verdade no backend)."""

from __future__ import annotations

from typing import FrozenSet

# Permissões nomeadas (alinhadas ao prompt_usermng.md)
PERMISSIONS: tuple[str, ...] = (
    "users.view",
    "users.create",
    "users.edit",
    "users.delete",
    "companies.view",
    "companies.create",
    "companies.edit",
    "companies.delete",
    "devices.view",
    "devices.create",
    "devices.edit",
    "devices.delete",
    "devices.test_connection",
    "devices.snmp_collect",
    "devices.snmp_refresh",
    "devices.ssh_collect",
    "bgp.view",
    "bgp.edit_role",
    "bgp.lookup",
    "interfaces.view",
    "logs.view",
)

ALL_PERMISSIONS: FrozenSet[str] = frozenset(PERMISSIONS)

_ROLE_MATRIX: dict[str, FrozenSet[str]] = {
    "superadmin": ALL_PERMISSIONS,
    "admin": frozenset(
        {
            "users.view",
            "users.create",
            "users.edit",
            "users.delete",
            "companies.view",
            "companies.create",
            "companies.edit",
            "companies.delete",
            "devices.view",
            "devices.create",
            "devices.edit",
            "devices.delete",
            "devices.test_connection",
            "devices.snmp_collect",
            "devices.snmp_refresh",
            "devices.ssh_collect",
            "bgp.view",
            "bgp.edit_role",
            "bgp.lookup",
            "interfaces.view",
            "logs.view",
        }
    ),
    "operator": frozenset(
        {
            "companies.view",
            "devices.view",
            "devices.create",
            "devices.edit",
            "devices.test_connection",
            "devices.snmp_collect",
            "devices.snmp_refresh",
            "devices.ssh_collect",
            "bgp.view",
            "bgp.edit_role",
            "bgp.lookup",
            "interfaces.view",
            "logs.view",
        }
    ),
    "viewer": frozenset(
        {
            "users.view",
            "companies.view",
            "devices.view",
            "bgp.view",
            "interfaces.view",
            "logs.view",
        }
    ),
}


def permissions_for_role(role: str) -> FrozenSet[str]:
    r = (role or "").strip().lower()
    return _ROLE_MATRIX.get(r, frozenset())


def role_has_permission(role: str, permission: str) -> bool:
    return permission in permissions_for_role(role)
