from contextlib import asynccontextmanager
import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .activity_log import configure_activity_logging
from .audit_log import configure_audit_logging
from .config import settings
from .database import apply_schema_patches, create_tables
from .middleware.user_audit_middleware import UserAuditMiddleware
from .routers import auth, communities, companies, devices, logs, management, snmp, users
from .routers.system_updates import router as system_updates_router
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
    version="0.1.1",
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

# Em qualquer ambiente exceto produção: regex para Vite em localhost/127/::1 e IPs RFC1918 com qualquer porta.
# (VITE_API_URL apontando para :8000 + página em http://192.168.x.x:5174 exige isto no preflight CORS.)
# Em produção use apenas ``allow_origins`` + ``CORS_EXTRA_ORIGINS`` (lista explícita).
_cors_origin_regex = None
if (settings.app_env or "").strip().lower() != "production":
    _cors_origin_regex = (
        r"^https?://("
        r"localhost|127\.0\.0\.1|\[::1\]|"
        r"192\.168\.\d{1,3}\.\d{1,3}|"
        r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
        r"172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}"
        r")(:\d+)?$"
    )

# FastAPI faz ``user_middleware.insert(0, …)``: o **último** ``add_middleware`` fica mais externo.
# CORS por último processa preflight e anexa cabeçalhos na resposta antes de outros middlewares.
app.add_middleware(UserAuditMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_base + _cors_extra,
    allow_origin_regex=_cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(companies.router)
app.include_router(devices.router)
app.include_router(communities.router)
app.include_router(snmp.router)
app.include_router(logs.router)
app.include_router(management.router)
app.include_router(system_updates_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
