from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Table,
    Text,
    UniqueConstraint,
    text as sqla_text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _now():
    return datetime.now(timezone.utc)


user_company = Table(
    "user_company",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("company_id", Integer, ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True),
)


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    devices: Mapped[list["Device"]] = relationship(back_populates="company")
    users: Mapped[list["User"]] = relationship(secondary=user_company, back_populates="companies")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="viewer", index=True)
    # True = vê/edita dispositivos de todas as empresas (exceto superadmin, que já tem escopo total).
    access_all_companies: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    companies: Mapped[list[Company]] = relationship(secondary=user_company, back_populates="users")


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("companies.id"),
        nullable=False,
        index=True,
        server_default=sqla_text("1"),
    )
    client: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    ip_address: Mapped[str] = mapped_column(String(45), unique=True, nullable=False, index=True)
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    vendor: Mapped[str] = mapped_column(String(50), default="Huawei")
    model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    username: Mapped[str] = mapped_column(String(50), nullable=False)
    password_encrypted: Mapped[str] = mapped_column(String(512), nullable=False)
    snmp_community: Mapped[str | None] = mapped_column(String(50), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_asn: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    configurations: Mapped[list["Configuration"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )
    interfaces: Mapped[list["Interface"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )
    bgp_peers: Mapped[list["BGPPeer"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )
    inventory_events: Mapped[list["InventoryHistory"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )
    device_vrfs: Mapped[list["DeviceVrf"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )
    prefix_lookup_history: Mapped[list["PrefixLookupHistory"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )
    community_library_items: Mapped[list["CommunityLibraryItem"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )
    community_sets: Mapped[list["CommunitySet"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )
    community_change_audits: Mapped[list["CommunityChangeAudit"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )
    device_community_lists: Mapped[list["DeviceCommunityList"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )
    community_sync_audits: Mapped[list["CommunitySyncAudit"]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )
    company: Mapped["Company"] = relationship(back_populates="devices")


class Configuration(Base):
    __tablename__ = "configurations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), nullable=False, index=True)
    config_text: Mapped[str] = mapped_column(Text, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Origem da sessão SSH (ex.: ssh_bgp_verbose); dedupe por ``content_sha256``.
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="ssh")
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    device: Mapped["Device"] = relationship(back_populates="configurations")


class Interface(Base):
    """
    Inventário de interface pertencente a um único `Device`.
    Chave natural: (device_id, name) — IPs/ASN de outros equipamentos nunca são misturados aqui.
    """

    __tablename__ = "interfaces"
    __table_args__ = (UniqueConstraint("device_id", "name", name="uq_interface_device_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    netmask: Mapped[str | None] = mapped_column(String(45), nullable=True)
    ipv6_addresses: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    admin_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    speed_mbps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    device: Mapped["Device"] = relationship(back_populates="interfaces")
    metrics: Mapped[list["InterfaceMetric"]] = relationship(
        back_populates="interface", cascade="all, delete-orphan"
    )


class InterfaceMetric(Base):
    __tablename__ = "interface_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    interface_id: Mapped[int] = mapped_column(ForeignKey("interfaces.id"), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    in_octets: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    out_octets: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    in_errors: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    out_errors: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    interface: Mapped["Interface"] = relationship(back_populates="metrics")


class BGPPeer(Base):
    """
    Sessão BGP descoberta no contexto de um único `Device`.
    Chave natural: (device_id, peer_ip, vrf_name) — `vrf_name` vazio = instância principal (global);
    a mesma combinação em outro `device_id` é outra entidade.
    """

    __tablename__ = "bgp_peers"
    __table_args__ = (
        UniqueConstraint("device_id", "peer_ip", "vrf_name", name="uq_bgp_peer_device_vrf"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), nullable=False, index=True)
    peer_ip: Mapped[str] = mapped_column(String(45), nullable=False)
    # Instância BGP: vrf_name vazio = Principal (global); não vazio = VPN-Instance Huawei.
    vrf_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    remote_asn: Mapped[int | None] = mapped_column(Integer, nullable=True)
    local_addr: Mapped[str | None] = mapped_column(String(45), nullable=True)
    in_updates: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    out_updates: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    uptime_secs: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_customer: Mapped[bool] = mapped_column(Boolean, default=True)
    is_provider: Mapped[bool] = mapped_column(Boolean, default=False)
    is_ix: Mapped[bool] = mapped_column(Boolean, default=False)
    is_cdn: Mapped[bool] = mapped_column(Boolean, default=False)
    is_ibgp: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # True após o peer aparecer em pelo menos uma coleta SNMP/SSH persistida (ou refresh SNMP).
    # Só então sumir do inventário marca is_active=False — evita "Inativo" em linhas nunca confirmadas.
    inventory_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Preenchidos na coleta quando o SSH `display bgp … peer verbose` está disponível (não vêm do SNMP).
    route_policy_import: Mapped[str | None] = mapped_column(String(512), nullable=True)
    route_policy_export: Mapped[str | None] = mapped_column(String(512), nullable=True)

    device: Mapped["Device"] = relationship(back_populates="bgp_peers")


class DeviceVrf(Base):
    """VRFs vistos na última coleta SNMP (para diff no próximo ciclo)."""

    __tablename__ = "device_vrfs"
    __table_args__ = (UniqueConstraint("device_id", "vrf_name", name="uq_device_vrf_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), nullable=False, index=True)
    vrf_name: Mapped[str] = mapped_column(String(128), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    device: Mapped["Device"] = relationship(back_populates="device_vrfs")


class InventoryHistory(Base):
    """Histórico de insert/update/delete do inventário coletado (e alterações manuais relacionadas)."""

    __tablename__ = "inventory_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(255), nullable=False)
    old_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    device: Mapped["Device"] = relationship(back_populates="inventory_events")


class PrefixLookupHistory(Base):
    """Histórico de consultas de prefixo/ASN para futura comparação de mudanças de anúncio."""

    __tablename__ = "prefix_lookup_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    query: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_query: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    route_found: Mapped[bool] = mapped_column(Boolean, default=False)
    from_peer_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    as_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin: Mapped[str | None] = mapped_column(String(16), nullable=True)
    advertised_to_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    device: Mapped["Device"] = relationship(back_populates="prefix_lookup_history")


class DeviceCommunityList(Base):
    """
    Snapshot de ``ip community-list NAME`` no equipamento (grupo de communities), por dispositivo.
    Preenchido na sincronização a partir do running-config; não confundir com ``CommunitySet`` (rascunhos da app).
    """

    __tablename__ = "device_community_lists"
    __table_args__ = (UniqueConstraint("device_id", "list_name", name="uq_device_community_list_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    list_name: Mapped[str] = mapped_column(String(128), nullable=False)
    communities_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    device: Mapped["Device"] = relationship(back_populates="device_community_lists")


class CommunityLibraryItem(Base):
    """
    Biblioteca: apenas ``ip community-filter`` (basic/advanced) + linhas manuais equivalentes.
    ``filter_name`` é o nome do filtro VRP (não confundir com ``ip community-list``).
    Chave natural: (device_id, filter_name, community_value, match_type).
    """

    __tablename__ = "community_library_items"
    __table_args__ = (
        UniqueConstraint(
            "device_id",
            "filter_name",
            "community_value",
            "match_type",
            name="uq_community_lib_device_filter_value_match",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    filter_name: Mapped[str] = mapped_column(String(128), nullable=False)
    community_value: Mapped[str] = mapped_column(String(512), nullable=False)
    match_type: Mapped[str] = mapped_column(String(16), nullable=False)  # basic | advanced | derived | legacy
    action: Mapped[str] = mapped_column(String(8), nullable=False, default="permit")  # permit | deny
    index_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    origin: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="discovered_running_config",
    )  # discovered_running_config | discovered_live | derived | manual
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags_json: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    device: Mapped["Device"] = relationship(back_populates="community_library_items")
    set_member_links: Mapped[list["CommunitySetMember"]] = relationship(
        back_populates="linked_library_item",
        foreign_keys="[CommunitySetMember.linked_library_item_id]",
        cascade="save-update, merge",
    )


class CommunitySet(Base):
    """Agrupamento ``ip community-list`` (importado ou criado na app); membros em ``CommunitySetMember``."""

    __tablename__ = "community_sets"
    __table_args__ = (
        UniqueConstraint("device_id", "slug", name="uq_community_set_device_slug"),
        UniqueConstraint("device_id", "vrp_object_name", name="uq_community_set_device_vrp_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    vrp_object_name: Mapped[str] = mapped_column(String(63), nullable=False)
    origin: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="app_created",
    )  # app_created | discovered_running_config | discovered_live | discovered (legado)
    discovered_members_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    """Legado: antes do sync com membros em ``community_set_members``; mantido só para leitura de dados antigos."""
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    device: Mapped["Device"] = relationship(back_populates="community_sets")
    members: Mapped[list["CommunitySetMember"]] = relationship(
        back_populates="community_set", cascade="all, delete-orphan", order_by="CommunitySetMember.position"
    )
    audits: Mapped[list["CommunityChangeAudit"]] = relationship(back_populates="community_set")


class CommunitySetMember(Base):
    """Membro de ``ip community-list``: valor explícito + vínculo opcional à biblioteca (community-filter)."""

    __tablename__ = "community_set_members"
    __table_args__ = (UniqueConstraint("community_set_id", "community_value", name="uq_set_member_set_value"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    community_set_id: Mapped[int] = mapped_column(
        ForeignKey("community_sets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    community_value: Mapped[str] = mapped_column(String(512), nullable=False)
    linked_library_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("community_library_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    missing_in_library: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    value_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    community_set: Mapped["CommunitySet"] = relationship(back_populates="members")
    linked_library_item: Mapped["CommunityLibraryItem | None"] = relationship(
        back_populates="set_member_links",
        foreign_keys="[CommunitySetMember.linked_library_item_id]",
    )


class CommunitySyncAudit(Base):
    """Auditoria de import/resync/reconciliação do módulo communities (sem apply automático)."""

    __tablename__ = "community_sync_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(24), nullable=False)  # running_config | live_device | migration | manual
    action: Mapped[str] = mapped_column(String(24), nullable=False)  # import | reconcile | sanitize | …
    details_json: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    device: Mapped["Device"] = relationship(back_populates="community_sync_audits")


class CommunityChangeAudit(Base):
    __tablename__ = "community_change_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    community_set_id: Mapped[int | None] = mapped_column(
        ForeignKey("community_sets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # preview | apply | rollback | delete
    candidate_config_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    command_sent_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    device_response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # success | failed | cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    device: Mapped["Device"] = relationship(back_populates="community_change_audits")
    community_set: Mapped["CommunitySet | None"] = relationship(back_populates="audits")


class SystemUpdateHistory(Base):
    """Histórico completo de updates do sistema (check/apply/rollback) via updater separado."""

    __tablename__ = "system_update_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    from_version: Mapped[str] = mapped_column(String(64), nullable=False)
    to_version: Mapped[str] = mapped_column(String(64), nullable=False)
    update_type: Mapped[str] = mapped_column(String(16), nullable=False)  # patch | minor | major
    triggered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    mode: Mapped[str] = mapped_column(String(24), nullable=False)  # check | manual | auto_patch | rollback
    status: Mapped[str] = mapped_column(String(24), nullable=False)  # in_progress | success | failed | rolled_back | blocked | checked
    log_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    triggered_user: Mapped["User | None"] = relationship("User", lazy="joined")
