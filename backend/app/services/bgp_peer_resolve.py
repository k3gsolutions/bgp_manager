"""
Correlação peer BGP ↔ interface (dados persistidos via SNMP no banco).

`interfaces` e `peers` passados às funções devem ser **do mesmo device_id** (mesmo
equipamento). Nunca misturar listas de dois roteadores: IPs/ASNs podem coincidir
entre dispositivos distintos.

Usado pela API de listagem de peers e pela investigação de prefixo (lookup),
para exibir `peer_name` sem `display bgp peer … verbose` por peer.
"""

from __future__ import annotations

import ipaddress
from typing import Any, Iterable


def _iface_ipv4_cidr(iface: Any) -> str | None:
    if not iface.ip_address:
        return None
    if "/" in str(iface.ip_address):
        return str(iface.ip_address).strip()
    if iface.netmask:
        return f"{iface.ip_address}/{iface.netmask}"
    return None


def _iface_ipv6_list(iface: Any) -> list[str]:
    raw = getattr(iface, "ipv6_addresses", None) or ""
    return [x.strip() for x in str(raw).split(",") if x.strip()]


def resolve_peer_local_and_name(
    peer_ip: str,
    explicit_local: str | None,
    interfaces: Iterable[Any],
) -> tuple[str | None, str | None]:
    """
    Retorna (local_addr inferido/corrigido, peer_name a partir da descrição da interface
    cuja sub-rede contém o peer_ip). Igual à lógica de `GET …/bgp-peers`.
    """
    local_fallback = explicit_local if explicit_local else None
    try:
        pip = ipaddress.ip_address(peer_ip.strip())
    except ValueError:
        return local_fallback, None

    for iface in interfaces:
        if pip.version == 4:
            iface_cidr = _iface_ipv4_cidr(iface)
            if not iface_cidr:
                continue
            try:
                iif = ipaddress.ip_interface(iface_cidr)
            except ValueError:
                continue
            if pip in iif.network:
                peer_name = (iface.description or "").strip() or None
                return str(iif.ip), peer_name
            continue

        for raw in _iface_ipv6_list(iface):
            try:
                if "/" in raw:
                    iif6 = ipaddress.ip_interface(raw)
                    if pip in iif6.network:
                        peer_name = (iface.description or "").strip() or None
                        return str(iif6.ip), peer_name
                else:
                    if pip == ipaddress.ip_address(raw):
                        peer_name = (iface.description or "").strip() or None
                        return raw, peer_name
            except ValueError:
                continue
    return local_fallback, None


def build_peer_hints_from_db(peers: Iterable[Any], interfaces: list[Any]) -> dict[str, dict[str, Any]]:
    """
    Mapa peer_ip → dados para UI / lookup (sem SSH).
    Se o mesmo IP existir na Principal e em VRF, prioriza a entrada da Principal (última escrita).
    """
    ifaces = list(interfaces)
    out: dict[str, dict[str, Any]] = {}
    plist = list(peers)
    # Peers com VRF primeiro, instância principal por último → sobrescreve e fica a Principal.
    plist.sort(key=lambda x: 0 if (getattr(x, "vrf_name", None) or "").strip() else 1)
    for p in plist:
        ip = getattr(p, "peer_ip", None)
        if not ip:
            continue
        vrf = (getattr(p, "vrf_name", None) or "").strip()
        _local, name = resolve_peer_local_and_name(ip, getattr(p, "local_addr", None), ifaces)
        out[str(ip)] = {
            "remote_asn": getattr(p, "remote_asn", None),
            "description": name,
            "display_name": (name or "").strip() or str(ip),
            "vrf_name": vrf,
        }
    return out
