from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_JWT_PLACEHOLDER = "dev-only-jwt-secret-change-me-or-set-JWT_SECRET"


class Settings(BaseSettings):
    # Exemplo local (sem credenciais reais); sobrescreva com DATABASE_URL no .env
    database_url: str = "sqlite+aiosqlite:///./bgpmanager.db"
    fernet_key: str = ""
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    # Lista separada por vírgulas, ex.: http://192.168.1.10:5174 (frontend por IP na LAN)
    cors_extra_origins: str = ""
    # JWT (obrigatório em produção; em development gera default inseguro se vazio)
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24
    # Senha inicial do superadmin (seed só cria usuário se ainda não existir nenhum)
    bootstrap_superadmin_username: str = "superadmin"
    bootstrap_superadmin_password: str = ""
    # Comandos opcionais para restart automático após atualização (executados via bash -lc).
    update_backend_restart_cmd: str = ""
    update_frontend_restart_cmd: str = ""
    # Máximo de snapshots ``display current-configuration`` por equipamento (mais antigos apagados).
    config_snapshot_retention: int = 30
    # Só voltar a executar ``display current-configuration`` no equipamento após esta janela (horas)
    # desde o último snapshot gravado — evita coleta a cada consulta SSH.
    config_snapshot_refresh_hours: float = 1.0

    # Versão da aplicação (usada no módulo de update remoto).
    # Em produção, o ideal é setar via env `APP_VERSION` no build do container.
    app_version: str = "0.1.0"

    # GitHub Releases (para consulta de updates).
    system_update_github_owner: str = "k3gsolutions"
    system_update_github_repo: str = "bgp_manager"

    # Docker/OCI image esperada (para updates versionados).
    # Ex.: ghcr.io/k3gsolutions/bgp_manager:vX.Y.Z
    system_update_ghcr_image_repo: str = "ghcr.io/k3gsolutions/bgp_manager"

    # Quando vazio, o updater tenta falhar com mensagem clara (sem auto-adivinhar).
    system_update_container_name: str = ""

    # Health-check do updater (sempre obrigatório; rollback se falhar).
    system_update_health_path: str = "/health"
    system_update_health_timeout_seconds: int = 60

    # Patch pode ser automático (opcional).
    # Mesmo com auto_patch=true, o modo de aplicação é decidido apenas pelo backend.
    system_update_auto_patch: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @model_validator(mode="after")
    def _jwt_and_production_secrets(self):
        env = (self.app_env or "").strip().lower()
        jwt = (self.jwt_secret or "").strip()
        fernet = (self.fernet_key or "").strip()

        if env == "production":
            if not jwt:
                raise ValueError(
                    "APP_ENV=production exige JWT_SECRET definido e forte no ambiente (não use valor vazio)."
                )
            if jwt == _DEV_JWT_PLACEHOLDER or len(jwt) < 32:
                raise ValueError(
                    "APP_ENV=production: JWT_SECRET deve ser uma string aleatória longa (≥ 32 caracteres), "
                    "nunca o valor de desenvolvimento."
                )
            if not fernet:
                raise ValueError(
                    "APP_ENV=production exige FERNET_KEY para cifrar credenciais SSH/SNMP dos equipamentos."
                )
            return self

        if not jwt:
            object.__setattr__(self, "jwt_secret", _DEV_JWT_PLACEHOLDER)
        return self


settings = Settings()
