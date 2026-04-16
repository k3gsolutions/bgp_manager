"""
Extrai definições e referências de BGP communities a partir de texto de running-config Huawei VRP.

Diferença VRP (ambas existem no mesmo ficheiro):
- ``ip community-filter …`` — filtro nomeado; cada ``index``/``permit|deny``/valor é uma entrada individual.
  O consumidor da app grava-os na **biblioteca** (``CommunityLibraryItem``).
- ``ip community-list NAME`` + linhas `` community VALUE`` — **grupo** de communities sob um nome.
  O consumidor grava em **Community sets** (``CommunitySet`` + membros na biblioteca ``derived`` quando necessário).

Também suporta:
- ``route-policy`` com ``if-match community-filter NAME``
- ``apply community`` (valores na mesma linha; sufixo ``additive`` removido)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal


def _norm_lines(text: str) -> list[str]:
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return t.split("\n")


def _strip_inline_comment(line: str) -> str:
    s = line.strip()
    if "#" in s:
        s = s.split("#", 1)[0].strip()
    return s


# ip community-filter basic NAME index 10 permit 100:1
_RE_FILTER = re.compile(
    r"^\s*ip\s+community-filter\s+(basic|advanced)\s+(\S+)\s+index\s+(\d+)\s+(permit|deny)\s+(.+?)\s*$",
    re.IGNORECASE,
)

# ip community-list NAME  (VRP; NAME até ao fim da linha útil)
_RE_LIST_HEADER = re.compile(r"^\s*ip\s+community-list\s+(\S+)\s*$", re.IGNORECASE)

# Linha `` community VALUE`` — com ou sem indentação obrigatória antes da palavra ``community``
# Valor + texto remanescente na linha (ex.: descrição livre no VRP)
_RE_LIST_LINE = re.compile(r"^\s*community\s+(\S+)(?:\s+(.*))?$", re.IGNORECASE)

# Valor sintético para listas cujo cabeçalho existe no running-config mas sem linhas `` community `` no backup
LIST_HEADER_EMPTY_VALUE = "[lista-sem-members-no-backup]"

# route-policy X permit|deny node N
_RE_RP_HEADER = re.compile(
    r"^\s*route-policy\s+(\S+)\s+(permit|deny)\s+node\s+(\d+)\s*$",
    re.IGNORECASE,
)

_RE_IF_MATCH_CF = re.compile(
    r"^\s*if-match\s+community-filter\s+(\S+)\s*$",
    re.IGNORECASE,
)

_RE_APPLY_COMM = re.compile(r"^\s*apply\s+community\s+(.+?)\s*$", re.IGNORECASE)


MatchType = Literal["basic", "advanced", "legacy"]


@dataclass(frozen=True)
class CommunityFilterEntry:
    match_type: Literal["basic", "advanced"]
    name: str
    index: int
    action: str
    value: str


@dataclass(frozen=True)
class CommunityListEntry:
    list_name: str
    value: str
    line_order: int
    value_description: str | None = None


@dataclass(frozen=True)
class RoutePolicyCommunityFilterRef:
    route_policy: str
    node: str
    filter_name: str


@dataclass(frozen=True)
class RoutePolicyApplyCommunity:
    route_policy: str
    node: str
    communities: tuple[str, ...]


@dataclass
class ParsedRunningConfigCommunities:
    community_filters: list[CommunityFilterEntry] = field(default_factory=list)
    community_lists: list[CommunityListEntry] = field(default_factory=list)
    route_policy_if_match: list[RoutePolicyCommunityFilterRef] = field(default_factory=list)
    route_policy_apply_community: list[RoutePolicyApplyCommunity] = field(default_factory=list)


def _dedupe_filters(items: list[CommunityFilterEntry]) -> list[CommunityFilterEntry]:
    seen: set[tuple[str, str, str, int, str]] = set()
    out: list[CommunityFilterEntry] = []
    for it in items:
        key = (it.match_type, it.name.lower(), it.value.strip(), it.index, it.action.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _dedupe_lists(items: list[CommunityListEntry]) -> list[CommunityListEntry]:
    seen: set[tuple[str, str]] = set()
    out: list[CommunityListEntry] = []
    for it in items:
        key = (it.list_name.lower(), it.value.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _split_apply_values(blob: str) -> tuple[str, ...]:
    s = _strip_inline_comment(blob).strip()
    if re.search(r"\sadditive\s*$", s, flags=re.I):
        s = re.sub(r"\sadditive\s*$", "", s, flags=re.I).strip()
    parts = s.split()
    vals: list[str] = []
    for p in parts:
        p = p.strip().strip(",").strip()
        if not p or p.lower() == "additive":
            continue
        vals.append(p)
    return tuple(vals)


def parse_running_config_communities(config_text: str) -> ParsedRunningConfigCommunities:
    lines = _norm_lines(config_text)
    out = ParsedRunningConfigCommunities()

    current_list: str | None = None
    list_order = 0
    list_header_order: list[str] = []

    rp_name: str | None = None
    rp_node: str | None = None

    for raw in lines:
        is_indented = bool(raw) and raw[0] in " \t"
        line = _strip_inline_comment(raw)
        if not line:
            continue

        matched_rp_header = False
        m_f = _RE_FILTER.match(line)
        if m_f:
            mt, name, idx_s, act, val = m_f.groups()
            try:
                idx = int(idx_s)
            except ValueError:
                continue
            val = (val or "").strip()
            if not name or not val:
                continue
            mt_l = (mt or "").lower()
            match_type: Literal["basic", "advanced"] = "advanced" if mt_l == "advanced" else "basic"
            out.community_filters.append(
                CommunityFilterEntry(
                    match_type=match_type,
                    name=name.strip(),
                    index=idx,
                    action=act.lower(),
                    value=val,
                )
            )
            current_list = None
            continue

        m_lh = _RE_LIST_HEADER.match(line)
        if m_lh:
            current_list = m_lh.group(1).strip()
            list_order = 0
            if current_list:
                list_header_order.append(current_list)
            continue

        if current_list:
            m_ll = _RE_LIST_LINE.match(line)
            if m_ll:
                val = (m_ll.group(1) or "").strip()
                extra = (m_ll.group(2) or "").strip()
                desc = extra or None
                if val:
                    list_order += 1
                    out.community_lists.append(
                        CommunityListEntry(
                            list_name=current_list,
                            value=val,
                            line_order=list_order,
                            value_description=desc,
                        )
                    )
                continue
            # Sai do bloco community-list ao encontrar outra declaração de topo comum
            if not is_indented:
                current_list = None

        m_rp = _RE_RP_HEADER.match(line)
        if m_rp:
            rp_name, _, rp_node = m_rp.group(1), m_rp.group(2), m_rp.group(3)
            matched_rp_header = True
            current_list = None
            continue

        if rp_name and rp_node:
            m_if = _RE_IF_MATCH_CF.match(line)
            if m_if:
                fn = m_if.group(1).strip()
                if fn:
                    out.route_policy_if_match.append(
                        RoutePolicyCommunityFilterRef(
                            route_policy=rp_name, node=str(rp_node), filter_name=fn
                        )
                    )
                continue
            m_ap = _RE_APPLY_COMM.match(line)
            if m_ap:
                vals = _split_apply_values(m_ap.group(1))
                if vals:
                    out.route_policy_apply_community.append(
                        RoutePolicyApplyCommunity(
                            route_policy=rp_name, node=str(rp_node), communities=vals
                        )
                    )
                continue

        # Sai do contexto route-policy em linhas de topo (não indentadas) que não são novo cabeçalho
        if rp_name and not is_indented and not matched_rp_header and not line.startswith("#"):
            rp_name = None
            rp_node = None

    out.community_filters = _dedupe_filters(out.community_filters)
    out.community_lists = _dedupe_lists(out.community_lists)

    # Cabeçalhos ``ip community-list NAME`` sem nenhuma linha `` community `` capturada
    names_with_members = {e.list_name for e in out.community_lists}
    seen_placeholder: set[str] = set()
    for h in list_header_order:
        if not h or h in names_with_members:
            continue
        if h in seen_placeholder:
            continue
        seen_placeholder.add(h)
        out.community_lists.append(
            CommunityListEntry(list_name=h, value=LIST_HEADER_EMPTY_VALUE, line_order=0)
        )
    out.community_lists = _dedupe_lists(out.community_lists)
    return out


def community_list_names_in_config(config_text: str) -> set[str]:
    """Nomes de ``ip community-list NAME`` presentes no texto (para deteção de conflito)."""
    names: set[str] = set()
    for e in parse_running_config_communities(config_text).community_lists:
        names.add(e.list_name)
    # cabeçalhos sem linhas filhas ainda aparecem se só houver header — re-scan headers
    for line in _norm_lines(config_text):
        line = _strip_inline_comment(line)
        m = _RE_LIST_HEADER.match(line)
        if m:
            names.add(m.group(1).strip())
    return names


def usage_counts_for_library_names(
    parsed: ParsedRunningConfigCommunities,
) -> dict[str, int]:
    """
    Conta referências ``if-match community-filter NAME`` por nome de filtro.
    """
    counts: dict[str, int] = {}
    for ref in parsed.route_policy_if_match:
        k = ref.filter_name
        counts[k] = counts.get(k, 0) + 1
    return counts


def format_phase1_community_list_block(vrp_object_name: str, community_values: list[str]) -> str:
    """Gera bloco VRP fase 1 (sem ``system-view`` / ``commit``)."""
    lines = [f"ip community-list {vrp_object_name}"]
    for v in community_values:
        vv = (v or "").strip()
        if vv:
            lines.append(f" community {vv}")
    return "\n".join(lines) + "\n"
