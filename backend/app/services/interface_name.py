from __future__ import annotations

import re


_RX_TRAILING_PAREN = re.compile(r"\s*\([^()]*\)\s*$")


def canonical_interface_name(name: str | None) -> str:
    """
    Normaliza nomes de interface vindos de fontes distintas (SNMP/SSH).

    Ex.: ``100GE0/3/0.25(40G)`` -> ``100GE0/3/0.25``
    """
    s = (name or "").strip()
    if not s:
        return ""
    prev = None
    # Remove sufixos finais "(...)" repetidos, mantendo o nome-base.
    while s and s != prev:
        prev = s
        s = _RX_TRAILING_PAREN.sub("", s).strip()
    return s

