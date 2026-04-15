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
                        created_at TIMESTAMP,
                        updated_at TIMESTAMP
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
                        created_at TIMESTAMP,
                        updated_at TIMESTAMP
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
                        created_at TIMESTAMP,
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
                        text("ALTER TABLE interfaces ADD COLUMN IF NOT EXISTS deactivated_at TIMESTAMP NULL")
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
                text("ALTER TABLE bgp_peers ADD COLUMN IF NOT EXISTS deactivated_at TIMESTAMP NULL")
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
                    text("ALTER TABLE interfaces ADD COLUMN IF NOT EXISTS deactivated_at TIMESTAMP NULL")
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


async def apply_schema_patches():
    async with engine.begin() as conn:

        def _rbac(c):
            _sync_rbac_schema_and_seed(c, c.dialect.name)

        await conn.run_sync(_rbac)
        await conn.run_sync(_sync_apply_schema_patches)
