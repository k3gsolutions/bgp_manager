"""
Parser de local-preference por route-policy a partir do backup running-config.
"""

from __future__ import annotations

import re


def parse_route_policy_local_preference(config_text: str, *, target_node: int = 3010) -> dict[str, int]:
    """
    Mapa ``route_policy_name -> local_preference``.

    Regra: usa o primeiro ``apply local-preference <N>`` encontrado
    **no node alvo** (por padrão, node 3010) de cada policy.
    """
    out: dict[str, int] = {}
    current_policy: str | None = None
    current_node: int | None = None

    # Alguns VRP trazem sufixos (ex.: description) na linha do node.
    rx_policy = re.compile(r"^\s*route-policy\s+(\S+)\s+(?:permit|deny)\s+node\s+(\d+)\b", re.I)
    # Alguns equipamentos trazem sufixos após o valor (ex.: additive).
    rx_apply = re.compile(r"^\s*apply\s+local-preference\s+(\d+)\b", re.I)

    for raw in (config_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "#":
            current_policy = None
            current_node = None
            continue
        m_pol = rx_policy.match(line)
        if m_pol:
            current_policy = (m_pol.group(1) or "").strip()
            try:
                current_node = int(m_pol.group(2))
            except (TypeError, ValueError):
                current_node = None
            continue
        if not current_policy or current_node != int(target_node):
            continue
        m_apply = rx_apply.match(line)
        if m_apply and current_policy not in out:
            out[current_policy] = int(m_apply.group(1))
    return out

