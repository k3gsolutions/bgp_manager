"""Schemas Pydantic — módulo BGP Communities (Huawei VRP, fase 1)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class CommunityLibraryItemOut(BaseModel):
    id: int
    device_id: int
    company_id: int
    filter_name: str
    community_value: str
    match_type: str
    action: str
    index_order: Optional[int] = None
    origin: str
    description: Optional[str] = None
    tags_json: Optional[dict | list] = None
    is_system: bool = False
    is_active: bool = True
    created_at: datetime
    updated_at: datetime
    usage_count: int = Field(0, description="Referências if-match community-filter com este nome (filtros).")

    model_config = {"from_attributes": True}


class CommunitySetMemberOut(BaseModel):
    id: Optional[int] = None
    position: int = 0
    community_value: str = ""
    linked_library_item_id: Optional[int] = None
    missing_in_library: bool = False
    linked_filter_name: str = Field("", description="Nome do community-filter na biblioteca, se resolvido.")
    value_description: Optional[str] = Field(None, description="Texto remanescente na linha ``community`` do VRP.")

    model_config = {"from_attributes": True}


class CommunitySetOut(BaseModel):
    id: int
    device_id: int
    company_id: int
    name: str
    slug: str
    vrp_object_name: str
    origin: str = Field(
        "app_created",
        description="app_created = criado na app; discovered_* / discovered = ``ip community-list`` importado.",
    )
    discovered_members: list[str] = Field(
        default_factory=list,
        description="Legado: membros só-JSON quando ainda não havia linhas em ``community_set_members``.",
    )
    implied_config_preview: Optional[str] = Field(
        None,
        description="Bloco VRP equivalente (só leitura) para sets descobertos.",
    )
    description: Optional[str] = None
    status: str
    created_by: Optional[int] = None
    updated_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    members: list[CommunitySetMemberOut] = Field(default_factory=list)
    members_total: int = Field(0, description="Número de membros no set.")
    members_resolved: int = Field(0, description="Membros com community-filter na biblioteca.")
    members_missing: int = Field(0, description="Membros sem ``community-filter`` com o mesmo valor.")

    model_config = {"from_attributes": True}


class CommunitySetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    slug: Optional[str] = Field(None, max_length=120, description="Opcional; gerado a partir do nome.")
    vrp_object_name: Optional[str] = Field(
        None,
        max_length=63,
        description="Nome do objeto ``ip community-list`` no VRP; se omitido, derivado do slug.",
    )
    description: Optional[str] = None
    member_library_item_ids: list[int] = Field(default_factory=list, description="Ordem preservada; sem duplicados.")


class CommunitySetUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    slug: Optional[str] = Field(None, max_length=120)
    vrp_object_name: Optional[str] = Field(None, max_length=63)
    description: Optional[str] = None
    member_library_item_ids: Optional[list[int]] = None


class CommunityPreviewOut(BaseModel):
    candidate_config_text: str
    candidate_sha256: str
    warnings: list[str] = Field(default_factory=list)
    members_missing_library: int = Field(0, description="Quantidade de valores sem filtro na biblioteca.")
    missing_community_values: list[str] = Field(
        default_factory=list,
        description="Valores do set sem ``ip community-filter`` correspondente na biblioteca.",
    )


class CommunityApplyRequest(BaseModel):
    confirm: bool = Field(False, description="Deve ser true após confirmação explícita do operador.")
    expected_candidate_sha256: str = Field(
        ...,
        min_length=64,
        max_length=64,
        description="SHA-256 hex devolvido pelo último preview bem-sucedido.",
    )
    acknowledge_missing_library_refs: bool = Field(
        False,
        description="Obrigatório true para aplicar quando existem valores sem community-filter na biblioteca.",
    )


class CommunityApplyResultOut(BaseModel):
    ok: bool
    status: str
    message: str = ""
    device_response_excerpt: Optional[str] = None


class CommunityResyncResult(BaseModel):
    inserted: int
    updated: int
    skipped_no_config: int
    skipped_manual: int
    community_filter_rows: int = Field(0, description="Linhas de community-filter consideradas no backup.")
    ip_community_list_rows: int = Field(
        0,
        description="Número de objetos ``ip community-list NAME`` no backup (um por nome).",
    )
    discovered_sets_synced: int = Field(
        0,
        description="Community sets ``origin=discovered`` gravados a partir do running-config.",
    )
    skipped_discovered_vrp_conflicts: int = Field(
        0,
        description="Listas omitidas por já existir set manual com o mesmo ``vrp_object_name``.",
    )
    wrong_library_rows_deactivated: int = Field(
        0,
        description="Linhas na biblioteca inativadas (importação errada a partir de ``ip community-list``).",
    )
    set_members_missing_library: int = Field(
        0,
        description="Total de membros de sets importados sem ``community-filter`` na biblioteca.",
    )


class CommunitySetCompareIn(BaseModel):
    set_id_a: int = Field(..., description="Primeiro set (mesmo dispositivo).")
    set_id_b: int = Field(..., description="Segundo set (mesmo dispositivo).")


class CommunitySetCompareOut(BaseModel):
    set_a_id: int
    set_b_id: int
    set_a_name: str
    set_b_name: str
    set_a_origin: str
    set_b_origin: str
    members_a_sorted: list[str] = Field(default_factory=list)
    members_b_sorted: list[str] = Field(default_factory=list)
    only_in_a: list[str] = Field(default_factory=list)
    only_in_b: list[str] = Field(default_factory=list)
    in_both: list[str] = Field(default_factory=list)


class CommunitySetCloneIn(BaseModel):
    name: Optional[str] = Field(None, max_length=200, description="Nome do novo set; se omitido, «Cópia de …».")


class CommunitySetUsageOut(BaseModel):
    community_set_id: int
    vrp_object_name: str
    vrp_name_conflict_in_saved_config: bool = False
    member_count: int
    members_resolved: int = 0
    members_missing: int = 0
    route_policy_references_sample: list[str] = Field(
        default_factory=list,
        description="Exemplos de route-policy / node que usam if-match community-filter (nomes de filtro).",
    )
