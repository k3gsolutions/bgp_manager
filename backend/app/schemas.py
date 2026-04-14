from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator
import ipaddress


class DeviceBase(BaseModel):
    company_id: int = Field(
        ...,
        ge=1,
        description="Cliente/empresa no cadastro — define o tenant e as permissões de acesso.",
    )
    client: Optional[str] = Field(
        None,
        max_length=100,
        description="Rótulo ou referência local (opcional); o controlo de acesso usa company_id.",
    )
    name: Optional[str] = Field(None, max_length=100, description="Hostname do dispositivo")
    ip_address: str = Field(..., description="Endereço IP do dispositivo")
    ssh_port: int = Field(22, ge=1, le=65535)
    vendor: str = Field("Huawei", max_length=50)
    model: Optional[str] = Field(None, max_length=50)
    username: str = Field(..., min_length=1, max_length=50)
    snmp_community: Optional[str] = Field(None, max_length=50)
    description: Optional[str] = None

    @field_validator("ip_address")
    @classmethod
    def validate_ip(cls, v: str) -> str:
        try:
            ipaddress.ip_address(v)
        except ValueError:
            raise ValueError(f"'{v}' não é um endereço IP válido")
        return v


class DeviceCreate(DeviceBase):
    password: str = Field(..., min_length=1, description="Senha SSH (será criptografada)")


class DeviceUpdate(BaseModel):
    company_id: int = Field(
        ...,
        ge=1,
        description="Obrigatório em cada atualização: o dispositivo deve permanecer atrelado a uma empresa.",
    )
    ip_address: Optional[str] = None
    ssh_port: Optional[int] = Field(None, ge=1, le=65535)
    vendor: Optional[str] = Field(None, max_length=50)
    model: Optional[str] = Field(None, max_length=50)
    username: Optional[str] = Field(None, min_length=1, max_length=50)
    password: Optional[str] = Field(None, min_length=1)
    snmp_community: Optional[str] = Field(None, max_length=50)
    description: Optional[str] = None

    @field_validator("ip_address")
    @classmethod
    def validate_ip(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        try:
            ipaddress.ip_address(v)
        except ValueError:
            raise ValueError(f"'{v}' não é um endereço IP válido")
        return v


class DeviceResponse(DeviceBase):
    id: int
    created_at: datetime
    updated_at: datetime
    local_asn: Optional[int] = Field(None, description="ASN local (atualizado na coleta SNMP)")
    company_name: Optional[str] = Field(None, description="Nome da empresa (join)")

    model_config = {"from_attributes": True}


class DeviceBatchImportFailure(BaseModel):
    index: int
    detail: str
    ip_address: Optional[str] = None


class DeviceBatchImportRequest(BaseModel):
    """Lista de objetos no mesmo formato de criação unitária (validação por item no servidor)."""
    devices: list[dict[str, Any]] = Field(..., min_length=1, max_length=500)


class DeviceBatchImportResponse(BaseModel):
    created: list[DeviceResponse] = Field(default_factory=list)
    failed: list[DeviceBatchImportFailure] = Field(default_factory=list)


class BGPPeerRoleUpdate(BaseModel):
    """Classificação do peer (Cliente / Operadora / IX / CDN) — exatamente um ativo."""

    is_customer: bool
    is_provider: bool
    is_ix: bool = False
    is_cdn: bool = False

    @model_validator(mode="after")
    def one_role(self):
        selected = int(self.is_customer) + int(self.is_provider) + int(self.is_ix) + int(self.is_cdn)
        if selected != 1:
            raise ValueError("Selecione exatamente um papel: Cliente, Operadora, IX ou CDN.")
        return self


class InventoryHistoryItem(BaseModel):
    id: int
    device_id: int
    created_at: datetime
    source: str
    entity_type: str
    action: str
    entity_key: str
    old_json: Optional[str] = None
    new_json: Optional[str] = None
    batch_id: Optional[str] = None

    model_config = {"from_attributes": True}


class BgpExportLookupRequest(BaseModel):
    """IP/prefixo IPv4 (ex: 203.0.113.0/24) ou ASN (ex: 64512 ou AS64512)."""

    query: str = Field(..., min_length=1, max_length=200)


class BgpProviderAdvertisedRequest(BaseModel):
    """SSH: lista prefixos advertidos ao peer (Operadora, IX ou CDN no banco)."""

    peer_id: int = Field(..., ge=1)
    offset: int = Field(0, ge=0, description="Paginação: índice inicial (0, 20, 40…).")
    fetch_all: bool = Field(
        False,
        description="Se true, retorna até o limite de exibição (200) numa única consulta para paginação local.",
    )


class BgpProviderAdvertisedItem(BaseModel):
    prefix: str
    as_path: Optional[str] = None


class BgpProviderAdvertisedResponse(BaseModel):
    peer_ip: str = ""
    vrf_name: str = ""
    too_many: bool = Field(
        False,
        description="Legado; substituído pelo truncamento com capped (máx. 200 rotas).",
    )
    error: Optional[str] = None
    message: Optional[str] = None
    total: int = Field(0, description="Prefixos considerados na paginação (máx. 200 se ``capped``).")
    reported_total: Optional[int] = Field(
        None,
        description="«Total Number of Routes» na saída VRP, quando presente.",
    )
    capped: bool = Field(False, description="True se a tabela tinha mais rotas que o limite de exibição.")
    full_total: Optional[int] = Field(
        None,
        description="Total de linhas parseadas antes do limite, quando ``capped``.",
    )
    offset: int = 0
    page_size: int = 20
    has_more: bool = False
    items: list[BgpProviderAdvertisedItem] = Field(default_factory=list)
    log: list[str] = Field(default_factory=list)


class BgpCustomerReceivedRequest(BgpProviderAdvertisedRequest):
    """SSH: lista prefixos recebidos do peer (peers marcados como Cliente no banco)."""

    pass


class BgpCustomerReceivedItem(BgpProviderAdvertisedItem):
    """Prefixo + AS-Path (coluna Path/Ogn na tabela received-routes)."""

    pass


class BgpCustomerReceivedResponse(BgpProviderAdvertisedResponse):
    """Mesmo envelope que advertised-routes; itens são prefixos recebidos."""

    items: list[BgpCustomerReceivedItem] = Field(default_factory=list)


class BgpExportLookupResponse(BaseModel):
    query: str
    route_found: bool
    prepend_detected: bool
    local_asn: Optional[int] = None
    # Best path attributes
    as_path: Optional[str] = None
    origin: Optional[str] = None          # igp | egp | ?
    local_pref: Optional[int] = None
    med: Optional[int] = None
    nexthop: Optional[str] = None
    from_peer_ip: Optional[str] = None
    # Communities (Standard / Extended / Large)
    communities: list[str] = Field(default_factory=list)
    ext_communities: list[str] = Field(default_factory=list)
    large_communities: list[str] = Field(default_factory=list)
    # Peer info (from `display bgp peer {IP} verbose`)
    from_peer: dict[str, Any] = Field(default_factory=dict)
    # Operator peers from DB + SSH advertised check
    operator_peers: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Peers marcados como Operadora/IX (banco) — eBGP",
    )
    advertised_peer_ips: list[str] = Field(
        default_factory=list,
        description="Lista extraída do bloco 'Advertised to such XX peers' no detalhe BGP.",
    )
    advertised_to: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Checagem advertised-routes por peer Operadora/IX (SSH + detail)",
    )
    # ASN query extras
    prefixes_found: list[str] = Field(default_factory=list)
    commands_tried: list[str] = Field(default_factory=list)
    raw_output: str = ""
    log: list[str] = Field(default_factory=list)


class DeviceConnectTest(BaseModel):
    success: bool
    message: str
    log: list[str] = Field(default_factory=list, description="Linhas de atividade (UI + console)")
    snmp: Optional[dict[str, Any]] = Field(
        None,
        description="Resumo da coleta SNMP após SSH (skipped/ok/error/contagens)",
    )


# ── Auth / RBAC ─────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=80)
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CompanyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class CompanyUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class CompanyResponse(BaseModel):
    id: int
    name: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=80)
    password: str = Field(..., min_length=8, max_length=128)
    role: Literal["superadmin", "admin", "operator", "viewer"] = "viewer"
    is_active: bool = True
    company_ids: list[int] = Field(default_factory=list)
    access_all_companies: bool = Field(
        default=False,
        description="Acesso a todos os clientes; só superadmin pode conceder. Ignorado para role superadmin.",
    )


class UserUpdate(BaseModel):
    username: Optional[str] = Field(None, min_length=1, max_length=80)
    role: Optional[Literal["superadmin", "admin", "operator", "viewer"]] = None
    is_active: Optional[bool] = None
    access_all_companies: Optional[bool] = None


class UserCompaniesPatch(BaseModel):
    company_ids: list[int] = Field(default_factory=list)
    access_all_companies: Optional[bool] = None


class UserPasswordPatch(BaseModel):
    password: str = Field(..., min_length=8, max_length=128)


class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    access_all_companies: bool = False
    company_ids: list[int] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MeResponse(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    access_all_companies: bool = False
    company_ids: list[int] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
