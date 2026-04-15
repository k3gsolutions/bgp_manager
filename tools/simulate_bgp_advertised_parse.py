#!/usr/bin/env python3
"""
Regressão: parse da lista 'Advertised to … peers' no output Huawei (VRP).

Reproduz o cenário VISION (45.179.44.0/23) em que a UI ficava sem peers:
- buffer sem linha em branco após o último IP;
- detail + basic concatenados;
- BOM / CR soltos.

Uso (na raiz do repositório):
  python tools/simulate_bgp_advertised_parse.py

Saída: exit 0 se todos os cenários passam; exit 1 caso contrário.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

from app.services.bgp_export_lookup import (  # noqa: E402
    advertised_peer_ips_from_huawei_routing_outputs,
)


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    fx = ROOT / "tools" / "fixtures" / "huawei_bgp_passo1_45_179_44.txt"
    if not fx.is_file():
        _fail(f"fixture em falta: {fx}")
    basic = fx.read_text(encoding="utf-8")

    n = len(advertised_peer_ips_from_huawei_routing_outputs("", basic))
    if n != 14:
        _fail(f"fixture isolada: esperado 14 peers, obteve {n}")

    # Detail primeiro no ficheiro mas só cabeçalho (sem IPs) — basic tem a lista completa
    detail_stub = (
        " BGP routing table entry information of 45.179.44.0/23:\n"
        " From: 0.0.0.0\n"
        " Advertised to such 14 peers:\n"
        " BGP attribute-set …\n"
    )
    n2 = len(advertised_peer_ips_from_huawei_routing_outputs(detail_stub, basic))
    if n2 != 14:
        _fail(f"detail+cabeçalho vazio + basic: esperado 14, obteve {n2}")

    # BOM no início (alguns buffers SSH / ficheiros Windows)
    n3 = len(advertised_peer_ips_from_huawei_routing_outputs("", "\ufeff" + basic.replace("\n", "\r\n")))
    if n3 != 14:
        _fail(f"BOM+CRLF: esperado 14, obteve {n3}")

    if advertised_peer_ips_from_huawei_routing_outputs("", ""):
        _fail("entradas vazias devem devolver lista vazia")

    print("OK: simulate_bgp_advertised_parse — cenários fixture, stub+detail, BOM/CRLF, vazio.")


if __name__ == "__main__":
    main()
