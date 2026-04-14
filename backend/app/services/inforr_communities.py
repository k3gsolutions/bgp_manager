"""
Regras INFORR / AS263934 para BGP Standard Community 64777:<valor>.

Formato operacional de circuito/CDN (parte baixa de 32 bits como inteiro):
  5CCYY  — ex.: 50705 = circuito 07, sufixo 05 (+4 prepends / 5 AS-Path na documentação).
  Não usar valores com YY=09 (conflitam semanticamente com YY=01); devem ser ignorados na análise.
"""

from __future__ import annotations

import re

_RE_STD = re.compile(r"^(\d+):(\d+)$")


def inforr_deprecated_09_low(low: int) -> bool:
    """True se o segundo número de 64777:low é tag ...09 legada (circuito ou CDN 81–83)."""
    if low % 100 != 9:
        return False
    if 50_000 <= low <= 59_999:
        return True
    if 58_100 <= low <= 58_399:
        return True
    return False


def filter_inforr_standard_communities(tokens: list[str] | None) -> list[str]:
    """Remove communities 64777:*09 obsoletas; mantém demais tokens (incl. NO-EXPORT, etc.)."""
    if not tokens:
        return []
    out: list[str] = []
    for tok in tokens:
        if tok is None:
            continue
        t = str(tok).strip()
        m = _RE_STD.match(t)
        if not m:
            out.append(t)
            continue
        high = int(m.group(1))
        low = int(m.group(2))
        if high == 64777 and inforr_deprecated_09_low(low):
            continue
        out.append(t)
    return out
