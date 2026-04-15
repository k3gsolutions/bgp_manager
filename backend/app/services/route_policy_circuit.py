"""
Modelos de nome de route-policy Huawei para derivar ID de circuito (CXX).

Formatos observados (OPERADORA opcional entre o ID e a função):

- ``C02-TIM-EXPORT``                     → CXX-[OPERADORA]-EXPORT
- ``C02-TIM-IMPORT-IPV4``                → CXX-[OPERADORA]-IMPORT-IPV4
- ``C02-TIM-IMPORT-IPV6``                → CXX-[OPERADORA]-IMPORT-IPV6
- ``C03-EXPORT``                         → CXX-EXPORT
- ``C03-IMPORT-IPV4``                    → CXX-IMPORT-IPV4
- ``C03-IMPORT-IPV6``                    → CXX-IMPORT-IPV6
- ``C04-EXPORT`` / ``C04-IMPORT-IPV4`` / ``C04-IMPORT-IPV6`` → idem

A parte funcional reconhecida no fim do sufixo (após ``Cxx-``) é uma de:
``EXPORT`` | ``IMPORT-IPV4`` | ``IMPORT-IPV6`` | ``IMPORT`` (genérico, se aparecer).

Tudo o que ficar entre ``Cxx-`` e essa cauda é tratado como *slug* da operadora (opcional),
ex.: ``TIM`` em ``C02-TIM-IMPORT-IPV4``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional

# Dois dígitos após C (ex.: C02, C03 — não confundir com C1 sem zero à esquerda nos exemplos).
_CIRCUIT_HEAD = re.compile(r"^\s*C(\d{2})\b", re.IGNORECASE)


@dataclass(frozen=True)
class RoutePolicyCircuitParse:
    """Resultado do parse de um nome de route-policy no padrão CXX-…"""

    raw: str
    circuit_id: str  # dois dígitos, ex. "02", "03"
    operator_slug: Optional[str]  # ex. "TIM"; None se ``C03-IMPORT-IPV4``
    function: Literal["export", "import", "import_ipv4", "import_ipv6"]


def extract_circuit_id(policy_name: str | None) -> Optional[str]:
    """
    Devolve só o ID numérico do circuito (string de 2 caracteres) ou None se não casar ``Cxx``.
    Não valida o restante do nome.
    """
    if not policy_name:
        return None
    m = _CIRCUIT_HEAD.match(policy_name.strip())
    return m.group(1) if m else None


def parse_route_policy_circuit(policy_name: str | None) -> Optional[RoutePolicyCircuitParse]:
    """
    Interpreta o nome completo da route-policy nos modelos descritos acima.

    Retorna None se não começar por ``C`` + dois dígitos, ou se o sufixo após o ID
    não for reconhecido como EXPORT / IMPORT / IMPORT-IPV4 / IMPORT-IPV6.
    """
    if not policy_name:
        return None
    raw = policy_name.strip()
    m = _CIRCUIT_HEAD.match(raw)
    if not m:
        return None
    circuit_id = m.group(1)
    rest = raw[m.end() :].lstrip("-")  # após C02 ou C02-
    if not rest:
        return None

    parts = [p for p in rest.split("-") if p]
    if not parts:
        return None
    u = [p.upper() for p in parts]

    # Caudas conhecidas (mais específicas primeiro).
    if len(u) >= 2 and u[-2] == "IMPORT" and u[-1] == "IPV4":
        fn: Literal["export", "import", "import_ipv4", "import_ipv6"] = "import_ipv4"
        op_parts = parts[:-2]
    elif len(u) >= 2 and u[-2] == "IMPORT" and u[-1] == "IPV6":
        fn = "import_ipv6"
        op_parts = parts[:-2]
    elif u[-1] == "EXPORT":
        fn = "export"
        op_parts = parts[:-1]
    elif u[-1] == "IMPORT":
        fn = "import"
        op_parts = parts[:-1]
    else:
        return None

    operator_slug = "-".join(op_parts).strip() or None
    return RoutePolicyCircuitParse(
        raw=raw,
        circuit_id=circuit_id,
        operator_slug=operator_slug,
        function=fn,
    )


def circuit_id_from_peer_policies(
    route_policy_import: str | None,
    route_policy_export: str | None,
) -> Optional[str]:
    """
    ID de circuito único a partir das policies de um peer: preferência IMPORT, depois EXPORT,
    só se ambos derivarem o mesmo ``circuit_id`` quando ambos existem; se divergirem, devolve None.
    """
    i = extract_circuit_id(route_policy_import)
    e = extract_circuit_id(route_policy_export)
    if i and e and i != e:
        return None
    return i or e
