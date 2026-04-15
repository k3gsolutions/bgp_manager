from contextlib import asynccontextmanager
import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .activity_log import configure_activity_logging
from .audit_log import configure_audit_logging
from .config import settings
from .database import apply_schema_patches, create_tables
from .middleware.user_audit_middleware import UserAuditMiddleware
from .routers import auth, companies, devices, logs, management, snmp, users
from .services.startup_checks import run_startup_access_checks


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_activity_logging()
    configure_audit_logging()
    await create_tables()
    await apply_schema_patches()
    asyncio.create_task(run_startup_access_checks())
    yield


app = FastAPI(
    title="BGP Manager API",
    description="Gerenciamento de dispositivos de rede — Huawei NE8000",
    version="0.1.0",
    lifespan=lifespan,
)

_cors_base = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
    "http://[::1]:5173",
    "http://[::1]:5174",
    "http://localhost:3000",
]
_cors_extra = [o.strip() for o in settings.cors_extra_origins.split(",") if o.strip()]

# Em desenvolvimento: aceita qualquer porta em localhost / 127.0.0.1 / ::1 e origens típicas na LAN
# (quando o Vite abre por http://192.168.x.x:5174 ou outra porta). O Origin NUNCA é a URL do API
# (ex.: :8000) — é sempre a página que está no navegador.
_cors_origin_regex = None
if (settings.app_env or "").strip().lower() == "development":
    _cors_origin_regex = (
        r"^https?://("
        r"localhost|127\.0\.0\.1|\[::1\]|"
        r"192\.168\.\d{1,3}\.\d{1,3}|"
        r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
        r"172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}"
        r")(:\d+)?$"
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_base + _cors_extra,
    allow_origin_regex=_cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(UserAuditMiddleware)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(companies.router)
app.include_router(devices.router)
app.include_router(snmp.router)
app.include_router(logs.router)
app.include_router(management.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
