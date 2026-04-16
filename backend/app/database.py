import warnings

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from .config import settings

# SQLite não suporta múltiplos escritores simultâneos.
# NullPool cria uma conexão nova por requisição e fecha imediatamente,
# evitando timeout/esgotamento do pool durante operações SNMP longas.
engine = create_async_engine(
    settings.database_url,
    echo=settings.app_env == "development",
    poolclass=NullPool,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


def _try_create_device_scoped_unique_index(
    connection,
    dialect: str,
    *,
    table: str,
    index_name: str,
    columns_sql: str,
    group_by_sql: str,
) -> None:
    """
    Garante índice único (device_id, …) para isolar inventário por equipamento.
    Se já existir duplicata lógica no banco, não cria o índice (evita quebrar o startup).
    """
    insp = inspect(connection)
    if not insp.has_table(table):
        return
    for uq in insp.get_unique_constraints(table):
        if uq.get("name") == index_name:
            return
    for idx in insp.get_indexes(table):
        if idx.get("name") == index_name:
            return
    dup = connection.execute(
        text(f"SELECT 1 FROM {table} GROUP BY {group_by_sql} HAVING COUNT(*) > 1 LIMIT 1")
    ).fetchone()
    if dup:
        warnings.warn(
            f"Tabela {table}: há linhas duplicadas para a mesma chave por dispositivo; "
            f"deduplique antes de aplicar {index_name}. Isolamento por device_id continua na aplicação.",
            UserWarning,
            stacklevel=2,
        )
        return
    if dialect == "sqlite":
        connection.execute(
            text(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table} ({columns_sql})")
        )
    else:
        connection.execute(
            text(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table} ({columns_sql})")
        )


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _sync_rbac_schema_and_seed(connection, dialect: str) -> None:
    """Tabelas RBAC, company_id em devices, empresa padrão e superadmin inicial (se vazio)."""
    insp = inspect(connection)

    if not insp.has_table("companies"):
        if dialect == "sqlite":
            connection.execute(
                text(
                    """
                    CREATE TABLE companies (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name VARCHAR(200) NOT NULL,
                        created_at DATETIME,
                        updated_at DATETIME
                    )
                    """
                )
            )
        else:
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS companies (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(200) NOT NULL,
                        created_at TIMESTAMPTZ,
                        updated_at TIMESTAMPTZ
                    )
                    """
                )
            )
        insp = inspect(connection)

    if not insp.has_table("users"):
        if dialect == "sqlite":
            connection.execute(
                text(
                    """
                    CREATE TABLE users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username VARCHAR(80) NOT NULL UNIQUE,
                        password_hash VARCHAR(255) NOT NULL,
                        role VARCHAR(32) NOT NULL DEFAULT 'viewer',
                        is_active BOOLEAN NOT NULL DEFAULT 1,
                        created_at DATETIME,
                        updated_at DATETIME
                    )
                    """
                )
            )
        else:
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        username VARCHAR(80) NOT NULL UNIQUE,
                        password_hash VARCHAR(255) NOT NULL,
                        role VARCHAR(32) NOT NULL DEFAULT 'viewer',
                        is_active BOOLEAN NOT NULL DEFAULT true,
                        created_at TIMESTAMPTZ,
                        updated_at TIMESTAMPTZ
                    )
                    """
                )
            )
        insp = inspect(connection)

    if not insp.has_table("user_company"):
        if dialect == "sqlite":
            connection.execute(
                text(
                    """
                    CREATE TABLE user_company (
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
                        PRIMARY KEY (user_id, company_id)
                    )
                    """
                )
            )
        else:
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS user_company (
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
                        PRIMARY KEY (user_id, company_id)
                    )
                    """
                )
            )

    n_comp = connection.execute(text("SELECT COUNT(*) FROM companies")).scalar() or 0
    if int(n_comp) == 0:
        if dialect == "sqlite":
            connection.execute(
                text(
                    "INSERT INTO companies (name, created_at, updated_at) "
                    "VALUES ('Empresa padrão', datetime('now'), datetime('now'))"
                )
            )
        else:
            connection.execute(
                text(
                    "INSERT INTO companies (name, created_at, updated_at) "
                    "VALUES ('Empresa padrão', NOW(), NOW())"
                )
            )

    row = connection.execute(text("SELECT id FROM companies ORDER BY id LIMIT 1")).fetchone()
    default_cid = int(row[0]) if row else 1

    if insp.has_table("devices"):
        dev_cols = {c["name"] for c in insp.get_columns("devices")}
        if "company_id" not in dev_cols:
            if dialect == "sqlite":
                connection.execute(text("ALTER TABLE devices ADD COLUMN company_id INTEGER"))
            else:
                connection.execute(
                    text("ALTER TABLE devices ADD COLUMN IF NOT EXISTS company_id INTEGER")
                )
            connection.execute(
                text("UPDATE devices SET company_id = :cid WHERE company_id IS NULL"),
                {"cid": default_cid},
            )

    if insp.has_table("users"):
        ucols = {c["name"] for c in insp.get_columns("users")}
        if "access_all_companies" not in ucols:
            if dialect == "sqlite":
                connection.execute(
                    text(
                        "ALTER TABLE users ADD COLUMN access_all_companies BOOLEAN NOT NULL DEFAULT 0"
                    )
                )
            else:
                connection.execute(
                    text(
                        "ALTER TABLE users ADD COLUMN IF NOT EXISTS access_all_companies "
                        "BOOLEAN NOT NULL DEFAULT false"
                    )
                )
        insp = inspect(connection)

    n_users = connection.execute(text("SELECT COUNT(*) FROM users")).scalar() or 0
    if int(n_users) == 0:
        from .config import settings
        from .services.passwords import hash_password

        pwd = (settings.bootstrap_superadmin_password or "").strip()
        if not pwd and (settings.app_env or "").lower() == "development":
            pwd = "ChangeMe!SuperAdmin"
        if pwd:
            h = hash_password(pwd)
            un = (settings.bootstrap_superadmin_username or "superadmin").strip() or "superadmin"
            if dialect == "sqlite":
                connection.execute(
                    text(
                        "INSERT INTO users (username, password_hash, role, access_all_companies, is_active, created_at, updated_at) "
                        "VALUES (:u, :h, 'superadmin', 1, 1, datetime('now'), datetime('now'))"
                    ),
                    {"u": un, "h": h},
                )
            else:
                connection.execute(
                    text(
                        "INSERT INTO users (username, password_hash, role, access_all_companies, is_active, created_at, updated_at) "
                        "VALUES (:u, :h, 'superadmin', true, true, NOW(), NOW())"
                    ),
                    {"u": un, "h": h},
                )


def _sync_apply_schema_patches(connection) -> None:
    """Adiciona colunas novas em BD já existente (create_all não altera tabelas antigas)."""
    insp = inspect(connection)
    dialect = connection.dialect.name

    if not insp.has_table("devices"):
        return

    # Histórico de consultas de prefixo (idempotente)
    if not insp.has_table("prefix_lookup_history"):
        if dialect == "sqlite":
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS prefix_lookup_history (
                        id INTEGER PRIMARY KEY,
                        device_id INTEGER NOT NULL,
                        created_at DATETIME,
                        query VARCHAR(200) NOT NULL,
                        normalized_query VARCHAR(200),
                        route_found BOOLEAN NOT NULL DEFAULT 0,
                        from_peer_ip VARCHAR(45),
                        as_path TEXT,
                        origin VARCHAR(16),
                        advertised_to_json TEXT,
                        result_json TEXT,
                        FOREIGN KEY(device_id) REFERENCES devices (id)
                    )
                    """
                )
            )
        else:
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS prefix_lookup_history (
                        id SERIAL PRIMARY KEY,
                        device_id INTEGER NOT NULL REFERENCES devices(id),
                        created_at TIMESTAMPTZ,
                        query VARCHAR(200) NOT NULL,
                        normalized_query VARCHAR(200),
                        route_found BOOLEAN NOT NULL DEFAULT false,
                        from_peer_ip VARCHAR(45),
                        as_path TEXT,
                        origin VARCHAR(16),
                        advertised_to_json TEXT,
                        result_json TEXT
                    )
                    """
                )
            )

    dev_cols = {c["name"] for c in insp.get_columns("devices")}
    if "local_asn" not in dev_cols:
        if dialect == "sqlite":
            connection.execute(text("ALTER TABLE devices ADD COLUMN local_asn INTEGER"))
        else:
            connection.execute(text("ALTER TABLE devices ADD COLUMN IF NOT EXISTS local_asn INTEGER"))

    if not insp.has_table("bgp_peers"):
        if insp.has_table("interfaces"):
            iface_cols = {c["name"] for c in insp.get_columns("interfaces")}
            if "ipv6_addresses" not in iface_cols:
                if dialect == "sqlite":
                    connection.execute(text("ALTER TABLE interfaces ADD COLUMN ipv6_addresses TEXT"))
                else:
                    connection.execute(
                        text("ALTER TABLE interfaces ADD COLUMN IF NOT EXISTS ipv6_addresses TEXT")
                    )
            if "is_active" not in iface_cols:
                if dialect == "sqlite":
                    connection.execute(text("ALTER TABLE interfaces ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"))
                else:
                    connection.execute(
                        text("ALTER TABLE interfaces ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true")
                    )
            if "deactivated_at" not in iface_cols:
                if dialect == "sqlite":
                    connection.execute(text("ALTER TABLE interfaces ADD COLUMN deactivated_at DATETIME"))
                else:
                    connection.execute(
                        text("ALTER TABLE interfaces ADD COLUMN IF NOT EXISTS deactivated_at TIMESTAMPTZ NULL")
                    )
        return

    peer_cols = {c["name"] for c in insp.get_columns("bgp_peers")}
    if "is_ibgp" not in peer_cols:
        if dialect == "sqlite":
            connection.execute(
                text("ALTER TABLE bgp_peers ADD COLUMN is_ibgp BOOLEAN NOT NULL DEFAULT 0")
            )
        else:
            connection.execute(
                text(
                    "ALTER TABLE bgp_peers ADD COLUMN IF NOT EXISTS is_ibgp BOOLEAN NOT NULL DEFAULT false"
                )
            )
    if "is_ix" not in peer_cols:
        if dialect == "sqlite":
            connection.execute(
                text("ALTER TABLE bgp_peers ADD COLUMN is_ix BOOLEAN NOT NULL DEFAULT 0")
            )
        else:
            connection.execute(
                text(
                    "ALTER TABLE bgp_peers ADD COLUMN IF NOT EXISTS is_ix BOOLEAN NOT NULL DEFAULT false"
                )
            )
    if "is_cdn" not in peer_cols:
        if dialect == "sqlite":
            connection.execute(
                text("ALTER TABLE bgp_peers ADD COLUMN is_cdn BOOLEAN NOT NULL DEFAULT 0")
            )
        else:
            connection.execute(
                text(
                    "ALTER TABLE bgp_peers ADD COLUMN IF NOT EXISTS is_cdn BOOLEAN NOT NULL DEFAULT false"
                )
            )
    if "is_active" not in peer_cols:
        if dialect == "sqlite":
            connection.execute(
                text("ALTER TABLE bgp_peers ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1")
            )
        else:
            connection.execute(
                text(
                    "ALTER TABLE bgp_peers ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true"
                )
            )
    if "deactivated_at" not in peer_cols:
        if dialect == "sqlite":
            connection.execute(text("ALTER TABLE bgp_peers ADD COLUMN deactivated_at DATETIME"))
        else:
            connection.execute(
                text("ALTER TABLE bgp_peers ADD COLUMN IF NOT EXISTS deactivated_at TIMESTAMPTZ NULL")
            )
    if "vrf_name" not in peer_cols:
        if dialect == "sqlite":
            connection.execute(
                text("ALTER TABLE bgp_peers ADD COLUMN vrf_name VARCHAR(128) NOT NULL DEFAULT ''")
            )
        else:
            connection.execute(
                text(
                    "ALTER TABLE bgp_peers ADD COLUMN IF NOT EXISTS vrf_name VARCHAR(128) NOT NULL DEFAULT ''"
                )
            )
    if "inventory_confirmed" not in peer_cols:
        if dialect == "sqlite":
            connection.execute(
                text(
                    "ALTER TABLE bgp_peers ADD COLUMN inventory_confirmed BOOLEAN NOT NULL DEFAULT 0"
                )
            )
        else:
            connection.execute(
                text(
                    "ALTER TABLE bgp_peers ADD COLUMN IF NOT EXISTS inventory_confirmed BOOLEAN NOT NULL DEFAULT false"
                )
            )
        # Peers já inativos antes do campo: tratamos como confirmados (comportamento legado).
        connection.execute(
            text("UPDATE bgp_peers SET inventory_confirmed = 1 WHERE is_active = 0")
        )
    if "route_policy_import" not in peer_cols:
        if dialect == "sqlite":
            connection.execute(
                text("ALTER TABLE bgp_peers ADD COLUMN route_policy_import VARCHAR(512)")
            )
        else:
            connection.execute(
                text(
                    "ALTER TABLE bgp_peers ADD COLUMN IF NOT EXISTS route_policy_import VARCHAR(512)"
                )
            )
    if "route_policy_export" not in peer_cols:
        if dialect == "sqlite":
            connection.execute(
                text("ALTER TABLE bgp_peers ADD COLUMN route_policy_export VARCHAR(512)")
            )
        else:
            connection.execute(
                text(
                    "ALTER TABLE bgp_peers ADD COLUMN IF NOT EXISTS route_policy_export VARCHAR(512)"
                )
            )

    if insp.has_table("configurations"):
        cfg_cols = {c["name"] for c in insp.get_columns("configurations")}
        if "source" not in cfg_cols:
            if dialect == "sqlite":
                connection.execute(
                    text(
                        "ALTER TABLE configurations ADD COLUMN source VARCHAR(40) NOT NULL DEFAULT 'ssh'"
                    )
                )
            else:
                connection.execute(
                    text(
                        "ALTER TABLE configurations ADD COLUMN IF NOT EXISTS source VARCHAR(40) NOT NULL DEFAULT 'ssh'"
                    )
                )
        if "content_sha256" not in cfg_cols:
            if dialect == "sqlite":
                connection.execute(
                    text("ALTER TABLE configurations ADD COLUMN content_sha256 VARCHAR(64)")
                )
            else:
                connection.execute(
                    text(
                        "ALTER TABLE configurations ADD COLUMN IF NOT EXISTS content_sha256 VARCHAR(64)"
                    )
                )
        if "byte_size" not in cfg_cols:
            if dialect == "sqlite":
                connection.execute(text("ALTER TABLE configurations ADD COLUMN byte_size INTEGER"))
            else:
                connection.execute(
                    text("ALTER TABLE configurations ADD COLUMN IF NOT EXISTS byte_size INTEGER")
                )

    if insp.has_table("interfaces"):
        iface_cols = {c["name"] for c in insp.get_columns("interfaces")}
        if "ipv6_addresses" not in iface_cols:
            if dialect == "sqlite":
                connection.execute(text("ALTER TABLE interfaces ADD COLUMN ipv6_addresses TEXT"))
            else:
                connection.execute(
                    text("ALTER TABLE interfaces ADD COLUMN IF NOT EXISTS ipv6_addresses TEXT")
                )
        if "is_active" not in iface_cols:
            if dialect == "sqlite":
                connection.execute(text("ALTER TABLE interfaces ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"))
            else:
                connection.execute(
                    text("ALTER TABLE interfaces ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true")
                )
        if "deactivated_at" not in iface_cols:
            if dialect == "sqlite":
                connection.execute(text("ALTER TABLE interfaces ADD COLUMN deactivated_at DATETIME"))
            else:
                connection.execute(
                    text("ALTER TABLE interfaces ADD COLUMN IF NOT EXISTS deactivated_at TIMESTAMPTZ NULL")
                )

    # ── BGP Communities (fase 1): biblioteca, sets, membros, auditoria ─────────
    if insp.has_table("devices") and not insp.has_table("community_library_items"):
        if dialect == "sqlite":
            connection.execute(
                text(
                    """
                    CREATE TABLE community_library_items (
                        id INTEGER PRIMARY KEY,
                        device_id INTEGER NOT NULL,
                        company_id INTEGER NOT NULL,
                        filter_name VARCHAR(128) NOT NULL,
                        community_value VARCHAR(512) NOT NULL,
                        match_type VARCHAR(16) NOT NULL,
                        action VARCHAR(8) NOT NULL DEFAULT 'permit',
                        index_order INTEGER,
                        origin VARCHAR(16) NOT NULL DEFAULT 'discovered',
                        description TEXT,
                        tags_json TEXT,
                        is_system BOOLEAN NOT NULL DEFAULT 0,
                        is_active BOOLEAN NOT NULL DEFAULT 1,
                        created_at DATETIME,
                        updated_at DATETIME,
                        FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE CASCADE,
                        FOREIGN KEY(company_id) REFERENCES companies (id),
                        CONSTRAINT uq_community_lib_device_filter_value_match
                            UNIQUE (device_id, filter_name, community_value, match_type)
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE community_sets (
                        id INTEGER PRIMARY KEY,
                        device_id INTEGER NOT NULL,
                        company_id INTEGER NOT NULL,
                        name VARCHAR(200) NOT NULL,
                        slug VARCHAR(120) NOT NULL,
                        vrp_object_name VARCHAR(63) NOT NULL,
                        origin VARCHAR(16) NOT NULL DEFAULT 'manual',
                        discovered_members_json TEXT,
                        description TEXT,
                        status VARCHAR(32) NOT NULL DEFAULT 'draft',
                        created_by INTEGER,
                        updated_by INTEGER,
                        created_at DATETIME,
                        updated_at DATETIME,
                        FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE CASCADE,
                        FOREIGN KEY(company_id) REFERENCES companies (id),
                        FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
                        FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE SET NULL,
                        CONSTRAINT uq_community_set_device_slug UNIQUE (device_id, slug),
                        CONSTRAINT uq_community_set_device_vrp_name UNIQUE (device_id, vrp_object_name)
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE community_set_members (
                        id INTEGER PRIMARY KEY,
                        community_set_id INTEGER NOT NULL,
                        community_value VARCHAR(512) NOT NULL,
                        linked_library_item_id INTEGER,
                        missing_in_library BOOLEAN NOT NULL DEFAULT 0,
                        value_description TEXT,
                        position INTEGER NOT NULL DEFAULT 0,
                        FOREIGN KEY(community_set_id) REFERENCES community_sets (id) ON DELETE CASCADE,
                        FOREIGN KEY(linked_library_item_id) REFERENCES community_library_items (id) ON DELETE SET NULL,
                        CONSTRAINT uq_set_member_set_value UNIQUE (community_set_id, community_value)
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE community_change_audit (
                        id INTEGER PRIMARY KEY,
                        device_id INTEGER NOT NULL,
                        community_set_id INTEGER,
                        user_id INTEGER,
                        action VARCHAR(16) NOT NULL,
                        candidate_config_text TEXT NOT NULL DEFAULT '',
                        command_sent_text TEXT,
                        device_response_text TEXT,
                        status VARCHAR(16) NOT NULL,
                        created_at DATETIME,
                        FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE CASCADE,
                        FOREIGN KEY(community_set_id) REFERENCES community_sets (id) ON DELETE SET NULL,
                        FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL
                    )
                    """
                )
            )
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_comm_lib_device ON community_library_items (device_id)")
            )
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_comm_sets_device ON community_sets (device_id)"))
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_comm_audit_device ON community_change_audit (device_id)")
            )
        else:
            connection.execute(
                text(
                    """
                    CREATE TABLE community_library_items (
                        id SERIAL PRIMARY KEY,
                        device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                        company_id INTEGER NOT NULL REFERENCES companies(id),
                        filter_name VARCHAR(128) NOT NULL,
                        community_value VARCHAR(512) NOT NULL,
                        match_type VARCHAR(16) NOT NULL,
                        action VARCHAR(8) NOT NULL DEFAULT 'permit',
                        index_order INTEGER,
                        origin VARCHAR(16) NOT NULL DEFAULT 'discovered',
                        description TEXT,
                        tags_json JSONB,
                        is_system BOOLEAN NOT NULL DEFAULT false,
                        is_active BOOLEAN NOT NULL DEFAULT true,
                        created_at TIMESTAMPTZ,
                        updated_at TIMESTAMPTZ,
                        CONSTRAINT uq_community_lib_device_filter_value_match
                            UNIQUE (device_id, filter_name, community_value, match_type)
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE community_sets (
                        id SERIAL PRIMARY KEY,
                        device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                        company_id INTEGER NOT NULL REFERENCES companies(id),
                        name VARCHAR(200) NOT NULL,
                        slug VARCHAR(120) NOT NULL,
                        vrp_object_name VARCHAR(63) NOT NULL,
                        origin VARCHAR(16) NOT NULL DEFAULT 'manual',
                        discovered_members_json JSONB,
                        description TEXT,
                        status VARCHAR(32) NOT NULL DEFAULT 'draft',
                        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                        updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                        created_at TIMESTAMPTZ,
                        updated_at TIMESTAMPTZ,
                        CONSTRAINT uq_community_set_device_slug UNIQUE (device_id, slug),
                        CONSTRAINT uq_community_set_device_vrp_name UNIQUE (device_id, vrp_object_name)
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE community_set_members (
                        id SERIAL PRIMARY KEY,
                        community_set_id INTEGER NOT NULL REFERENCES community_sets(id) ON DELETE CASCADE,
                        community_value VARCHAR(512) NOT NULL,
                        linked_library_item_id INTEGER REFERENCES community_library_items(id) ON DELETE SET NULL,
                        missing_in_library BOOLEAN NOT NULL DEFAULT false,
                        value_description TEXT,
                        position INTEGER NOT NULL DEFAULT 0,
                        CONSTRAINT uq_set_member_set_value UNIQUE (community_set_id, community_value)
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE community_change_audit (
                        id SERIAL PRIMARY KEY,
                        device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                        community_set_id INTEGER REFERENCES community_sets(id) ON DELETE SET NULL,
                        user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                        action VARCHAR(16) NOT NULL,
                        candidate_config_text TEXT NOT NULL DEFAULT '',
                        command_sent_text TEXT,
                        device_response_text TEXT,
                        status VARCHAR(16) NOT NULL,
                        created_at TIMESTAMPTZ
                    )
                    """
                )
            )
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_comm_lib_device ON community_library_items (device_id)")
            )
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_comm_sets_device ON community_sets (device_id)"))
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_comm_audit_device ON community_change_audit (device_id)")
            )

    # ip community-list (grupos) — snapshot por dispositivo
    if insp.has_table("devices") and not insp.has_table("device_community_lists"):
        if dialect == "sqlite":
            connection.execute(
                text(
                    """
                    CREATE TABLE device_community_lists (
                        id INTEGER PRIMARY KEY,
                        device_id INTEGER NOT NULL,
                        company_id INTEGER NOT NULL,
                        list_name VARCHAR(128) NOT NULL,
                        communities_json TEXT NOT NULL,
                        created_at DATETIME,
                        updated_at DATETIME,
                        FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE CASCADE,
                        FOREIGN KEY(company_id) REFERENCES companies (id),
                        CONSTRAINT uq_device_community_list_name UNIQUE (device_id, list_name)
                    )
                    """
                )
            )
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_dev_comm_list_device ON device_community_lists (device_id)")
            )
        else:
            connection.execute(
                text(
                    """
                    CREATE TABLE device_community_lists (
                        id SERIAL PRIMARY KEY,
                        device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                        company_id INTEGER NOT NULL REFERENCES companies(id),
                        list_name VARCHAR(128) NOT NULL,
                        communities_json JSONB NOT NULL,
                        created_at TIMESTAMPTZ,
                        updated_at TIMESTAMPTZ,
                        CONSTRAINT uq_device_community_list_name UNIQUE (device_id, list_name)
                    )
                    """
                )
            )
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_dev_comm_list_device ON device_community_lists (device_id)")
            )

    if insp.has_table("community_sets"):
        cs_cols = {c["name"] for c in insp.get_columns("community_sets")}
        if "origin" not in cs_cols:
            if dialect == "sqlite":
                connection.execute(
                    text(
                        "ALTER TABLE community_sets ADD COLUMN origin VARCHAR(16) NOT NULL DEFAULT 'manual'"
                    )
                )
            else:
                connection.execute(
                    text(
                        "ALTER TABLE community_sets ADD COLUMN IF NOT EXISTS origin VARCHAR(16) NOT NULL DEFAULT 'manual'"
                    )
                )
        if "discovered_members_json" not in cs_cols:
            if dialect == "sqlite":
                connection.execute(
                    text("ALTER TABLE community_sets ADD COLUMN discovered_members_json TEXT")
                )
            else:
                connection.execute(
                    text(
                        "ALTER TABLE community_sets ADD COLUMN IF NOT EXISTS discovered_members_json JSONB"
                    )
                )
        if "is_active" not in cs_cols:
            if dialect == "sqlite":
                connection.execute(
                    text("ALTER TABLE community_sets ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1")
                )
            else:
                connection.execute(
                    text(
                        "ALTER TABLE community_sets ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true"
                    )
                )
                connection.execute(text("UPDATE community_sets SET is_active = true WHERE is_active IS NULL"))

    # Communities v2: biblioteca ``filter_name``; membros do set por ``community_value`` + vínculo opcional
    if insp.has_table("community_library_items"):
        lib_cols_v2 = {c["name"] for c in insp.get_columns("community_library_items")}
        if "filter_name" not in lib_cols_v2 and "name" in lib_cols_v2:
            if dialect == "sqlite":
                connection.execute(text("ALTER TABLE community_library_items RENAME COLUMN name TO filter_name"))
            else:
                connection.execute(text("ALTER TABLE community_library_items RENAME COLUMN name TO filter_name"))
                connection.execute(
                    text(
                        "ALTER TABLE community_library_items DROP CONSTRAINT IF EXISTS "
                        "uq_community_lib_device_name_value_match"
                    )
                )
                connection.execute(
                    text(
                        "ALTER TABLE community_library_items ADD CONSTRAINT uq_community_lib_device_filter_value_match "
                        "UNIQUE (device_id, filter_name, community_value, match_type)"
                    )
                )

    if insp.has_table("community_set_members"):
        mcols_v2 = {c["name"] for c in insp.get_columns("community_set_members")}
        if "community_library_item_id" in mcols_v2 and "community_value" not in mcols_v2:
            if dialect == "sqlite":
                connection.execute(
                    text(
                        "ALTER TABLE community_set_members ADD COLUMN community_value VARCHAR(512) NOT NULL DEFAULT ''"
                    )
                )
                connection.execute(
                    text(
                        """
                        UPDATE community_set_members SET community_value = (
                            SELECT COALESCE(l.community_value, '')
                            FROM community_library_items l
                            WHERE l.id = community_set_members.community_library_item_id
                        )
                        """
                    )
                )
                connection.execute(
                    text("ALTER TABLE community_set_members ADD COLUMN linked_library_item_id INTEGER")
                )
                connection.execute(
                    text(
                        "UPDATE community_set_members SET linked_library_item_id = community_library_item_id"
                    )
                )
                connection.execute(
                    text(
                        "ALTER TABLE community_set_members ADD COLUMN missing_in_library BOOLEAN NOT NULL DEFAULT 0"
                    )
                )
                connection.execute(text("ALTER TABLE community_set_members ADD COLUMN value_description TEXT"))
                connection.execute(
                    text(
                        """
                        UPDATE community_set_members SET missing_in_library = 1
                        WHERE linked_library_item_id IS NULL
                           OR TRIM(community_value) = ''
                           OR NOT EXISTS (
                               SELECT 1 FROM community_library_items li
                               WHERE li.id = community_set_members.linked_library_item_id
                                 AND li.match_type IN ('basic', 'advanced')
                           )
                        """
                    )
                )
                connection.execute(
                    text("CREATE INDEX IF NOT EXISTS ix_set_member_linked ON community_set_members (linked_library_item_id)")
                )
                # SQLite antigo pode não suportar DROP COLUMN; nesse caso recriamos a tabela no layout v2.
                try:
                    connection.execute(
                        text("ALTER TABLE community_set_members DROP COLUMN community_library_item_id")
                    )
                    connection.execute(text("DROP INDEX IF EXISTS uq_set_member_unique_item"))
                    connection.execute(
                        text(
                            "CREATE UNIQUE INDEX IF NOT EXISTS uq_set_member_set_value "
                            "ON community_set_members (community_set_id, community_value)"
                        )
                    )
                except Exception:
                    connection.execute(text("PRAGMA foreign_keys=OFF"))
                    connection.execute(
                        text(
                            """
                            CREATE TABLE IF NOT EXISTS community_set_members_v2 (
                                id INTEGER PRIMARY KEY,
                                community_set_id INTEGER NOT NULL,
                                community_value VARCHAR(512) NOT NULL,
                                linked_library_item_id INTEGER,
                                missing_in_library BOOLEAN NOT NULL DEFAULT 0,
                                value_description TEXT,
                                position INTEGER NOT NULL DEFAULT 0,
                                FOREIGN KEY(community_set_id) REFERENCES community_sets (id) ON DELETE CASCADE,
                                FOREIGN KEY(linked_library_item_id) REFERENCES community_library_items (id) ON DELETE SET NULL,
                                CONSTRAINT uq_set_member_set_value UNIQUE (community_set_id, community_value)
                            )
                            """
                        )
                    )
                    connection.execute(
                        text(
                            """
                            INSERT OR REPLACE INTO community_set_members_v2
                                (id, community_set_id, community_value, linked_library_item_id, missing_in_library, value_description, position)
                            SELECT
                                id,
                                community_set_id,
                                COALESCE(NULLIF(TRIM(community_value), ''), '[unknown]'),
                                linked_library_item_id,
                                COALESCE(missing_in_library, 0),
                                value_description,
                                COALESCE(position, 0)
                            FROM community_set_members
                            """
                        )
                    )
                    connection.execute(text("DROP TABLE community_set_members"))
                    connection.execute(text("ALTER TABLE community_set_members_v2 RENAME TO community_set_members"))
                    connection.execute(
                        text(
                            "CREATE INDEX IF NOT EXISTS ix_set_member_linked ON community_set_members (linked_library_item_id)"
                        )
                    )
                    connection.execute(text("PRAGMA foreign_keys=ON"))
            else:
                connection.execute(
                    text(
                        "ALTER TABLE community_set_members ADD COLUMN IF NOT EXISTS community_value VARCHAR(512) NOT NULL DEFAULT ''"
                    )
                )
                connection.execute(
                    text(
                        """
                        UPDATE community_set_members AS m SET community_value = COALESCE(
                            (SELECT l.community_value FROM community_library_items l WHERE l.id = m.community_library_item_id),
                            ''
                        )
                        """
                    )
                )
                connection.execute(
                    text(
                        "ALTER TABLE community_set_members ADD COLUMN IF NOT EXISTS linked_library_item_id INTEGER REFERENCES community_library_items(id) ON DELETE SET NULL"
                    )
                )
                connection.execute(
                    text(
                        "UPDATE community_set_members SET linked_library_item_id = community_library_item_id "
                        "WHERE community_library_item_id IS NOT NULL"
                    )
                )
                connection.execute(
                    text(
                        "ALTER TABLE community_set_members ADD COLUMN IF NOT EXISTS missing_in_library BOOLEAN NOT NULL DEFAULT false"
                    )
                )
                connection.execute(
                    text(
                        "ALTER TABLE community_set_members ADD COLUMN IF NOT EXISTS value_description TEXT"
                    )
                )
                connection.execute(
                    text(
                        """
                        UPDATE community_set_members m SET missing_in_library = true
                        WHERE m.linked_library_item_id IS NULL
                           OR TRIM(m.community_value) = ''
                           OR NOT EXISTS (
                               SELECT 1 FROM community_library_items li
                               WHERE li.id = m.linked_library_item_id
                                 AND li.match_type IN ('basic', 'advanced')
                           )
                        """
                    )
                )
                connection.execute(
                    text(
                        "ALTER TABLE community_set_members DROP CONSTRAINT IF EXISTS uq_set_member_unique_item"
                    )
                )
                connection.execute(
                    text(
                        "ALTER TABLE community_set_members DROP COLUMN IF EXISTS community_library_item_id"
                    )
                )
                connection.execute(
                    text(
                        "ALTER TABLE community_set_members ADD CONSTRAINT uq_set_member_set_value "
                        "UNIQUE (community_set_id, community_value)"
                    )
                )
                connection.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_set_member_linked ON community_set_members (linked_library_item_id)"
                    )
                )
        # Estado híbrido (migração parcial): coluna antiga ainda existe e quebra inserts novos.
        if dialect == "sqlite":
            mcols_now = {c["name"] for c in inspect(connection).get_columns("community_set_members")}
            if "community_library_item_id" in mcols_now and "community_value" in mcols_now:
                connection.execute(text("PRAGMA foreign_keys=OFF"))
                connection.execute(
                    text(
                        """
                        CREATE TABLE IF NOT EXISTS community_set_members_v2 (
                            id INTEGER PRIMARY KEY,
                            community_set_id INTEGER NOT NULL,
                            community_value VARCHAR(512) NOT NULL,
                            linked_library_item_id INTEGER,
                            missing_in_library BOOLEAN NOT NULL DEFAULT 0,
                            value_description TEXT,
                            position INTEGER NOT NULL DEFAULT 0,
                            FOREIGN KEY(community_set_id) REFERENCES community_sets (id) ON DELETE CASCADE,
                            FOREIGN KEY(linked_library_item_id) REFERENCES community_library_items (id) ON DELETE SET NULL,
                            CONSTRAINT uq_set_member_set_value UNIQUE (community_set_id, community_value)
                        )
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        INSERT OR REPLACE INTO community_set_members_v2
                            (id, community_set_id, community_value, linked_library_item_id, missing_in_library, value_description, position)
                        SELECT
                            id,
                            community_set_id,
                            COALESCE(
                                NULLIF(TRIM(community_value), ''),
                                (SELECT COALESCE(li.community_value, '[unknown]')
                                 FROM community_library_items li
                                 WHERE li.id = community_set_members.community_library_item_id),
                                '[unknown]'
                            ),
                            linked_library_item_id,
                            COALESCE(missing_in_library, 0),
                            value_description,
                            COALESCE(position, 0)
                        FROM community_set_members
                        """
                    )
                )
                connection.execute(text("DROP TABLE community_set_members"))
                connection.execute(text("ALTER TABLE community_set_members_v2 RENAME TO community_set_members"))
                connection.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_set_member_linked ON community_set_members (linked_library_item_id)"
                    )
                )
                connection.execute(text("PRAGMA foreign_keys=ON"))

    if insp.has_table("community_library_items"):
        lib_cols = {c["name"] for c in insp.get_columns("community_library_items")}
        if "is_active" not in lib_cols:
            if dialect == "sqlite":
                connection.execute(
                    text(
                        "ALTER TABLE community_library_items ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"
                    )
                )
            else:
                connection.execute(
                    text(
                        "ALTER TABLE community_library_items ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true"
                    )
                )
                connection.execute(
                    text("UPDATE community_library_items SET is_active = true WHERE is_active IS NULL")
                )

    if insp.has_table("devices") and not insp.has_table("community_sync_audit"):
        if dialect == "sqlite":
            connection.execute(
                text(
                    """
                    CREATE TABLE community_sync_audit (
                        id INTEGER PRIMARY KEY,
                        device_id INTEGER NOT NULL,
                        user_id INTEGER,
                        source VARCHAR(24) NOT NULL,
                        action VARCHAR(24) NOT NULL,
                        details_json TEXT,
                        status VARCHAR(16) NOT NULL,
                        created_at DATETIME,
                        FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE CASCADE,
                        FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL
                    )
                    """
                )
            )
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_comm_sync_audit_device ON community_sync_audit (device_id)")
            )
        else:
            connection.execute(
                text(
                    """
                    CREATE TABLE community_sync_audit (
                        id SERIAL PRIMARY KEY,
                        device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
                        user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                        source VARCHAR(24) NOT NULL,
                        action VARCHAR(24) NOT NULL,
                        details_json JSONB,
                        status VARCHAR(16) NOT NULL,
                        created_at TIMESTAMPTZ
                    )
                    """
                )
            )
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_comm_sync_audit_device ON community_sync_audit (device_id)")
            )

    if dialect == "postgresql":
        for tbl in ("community_library_items", "community_sets"):
            if not insp.has_table(tbl):
                continue
            row = connection.execute(
                text(
                    """
                    SELECT character_maximum_length
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = :t
                      AND column_name = 'origin'
                    """
                ),
                {"t": tbl},
            ).fetchone()
            ml = row[0] if row else None
            if ml is not None and ml < 40:
                connection.execute(text(f'ALTER TABLE "{tbl}" ALTER COLUMN origin TYPE VARCHAR(40)'))

    # Migração de rótulos de origem/status (communities Huawei)
    if insp.has_table("community_sets"):
        connection.execute(
            text("UPDATE community_sets SET origin = 'app_created' WHERE origin = 'manual'")
        )
        connection.execute(
            text(
                "UPDATE community_sets SET origin = 'discovered_running_config' WHERE origin = 'discovered'"
            )
        )
        connection.execute(text("UPDATE community_sets SET status = 'imported' WHERE status = 'read_only'"))
    if insp.has_table("community_library_items"):
        connection.execute(
            text(
                "UPDATE community_library_items SET origin = 'discovered_running_config' "
                "WHERE origin = 'discovered' AND match_type IN ('basic', 'advanced')"
            )
        )

    # Cada dispositivo é uma entidade: interfaces e peers não se misturam entre devices.
    _try_create_device_scoped_unique_index(
        connection,
        dialect,
        table="interfaces",
        index_name="uq_interface_device_name",
        columns_sql="device_id, name",
        group_by_sql="device_id, name",
    )
    # Índice único antigo (device_id, peer_ip) — substituído por (device_id, peer_ip, vrf_name).
    if insp.has_table("bgp_peers"):
        if dialect == "sqlite":
            connection.execute(text("DROP INDEX IF EXISTS uq_bgp_peer_device_ip"))
        else:
            connection.execute(
                text("ALTER TABLE bgp_peers DROP CONSTRAINT IF EXISTS uq_bgp_peer_device_ip")
            )
            connection.execute(text("DROP INDEX IF EXISTS uq_bgp_peer_device_ip"))
    _try_create_device_scoped_unique_index(
        connection,
        dialect,
        table="bgp_peers",
        index_name="uq_bgp_peer_device_vrf",
        columns_sql="device_id, peer_ip, vrf_name",
        group_by_sql="device_id, peer_ip, vrf_name",
    )


def _sync_postgresql_naive_timestamp_to_timestamptz(connection) -> None:
    """
    BDs PostgreSQL antigos (TIMESTAMP sem TZ) falham com datetime timezone-aware do SQLAlchemy.
    Converte colunas conhecidas para TIMESTAMPTZ; valores naive existentes são tratados como UTC.
    """
    if connection.dialect.name != "postgresql":
        return
    insp = inspect(connection)
    pairs = [
        ("companies", "created_at"),
        ("companies", "updated_at"),
        ("users", "created_at"),
        ("users", "updated_at"),
        ("devices", "created_at"),
        ("devices", "updated_at"),
        ("configurations", "collected_at"),
        ("interfaces", "deactivated_at"),
        ("interfaces", "last_updated"),
        ("interface_metrics", "timestamp"),
        ("bgp_peers", "deactivated_at"),
        ("bgp_peers", "last_updated"),
        ("device_vrfs", "last_seen_at"),
        ("inventory_history", "created_at"),
        ("prefix_lookup_history", "created_at"),
        ("community_library_items", "created_at"),
        ("community_library_items", "updated_at"),
        ("community_sets", "created_at"),
        ("community_sets", "updated_at"),
        ("community_change_audit", "created_at"),
        ("device_community_lists", "created_at"),
        ("device_community_lists", "updated_at"),
        ("community_sync_audit", "created_at"),
    ]
    for table, column in pairs:
        if not insp.has_table(table):
            continue
        row = connection.execute(
            text(
                """
                SELECT data_type
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = :t
                  AND column_name = :c
                """
            ),
            {"t": table, "c": column},
        ).fetchone()
        if not row or row[0] != "timestamp without time zone":
            continue
        connection.execute(
            text(
                f"ALTER TABLE {table} ALTER COLUMN {column} TYPE TIMESTAMPTZ "
                f"USING {column} AT TIME ZONE 'UTC'"
            )
        )


async def apply_schema_patches():
    async with engine.begin() as conn:

        def _rbac(c):
            _sync_rbac_schema_and_seed(c, c.dialect.name)

        await conn.run_sync(_rbac)
        await conn.run_sync(_sync_apply_schema_patches)
        await conn.run_sync(_sync_postgresql_naive_timestamp_to_timestamptz)
