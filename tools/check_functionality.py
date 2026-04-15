#!/usr/bin/env python3
"""
Verificação rápida do backend (sem servidor manual): BD SQLite temporário,
lifespan completo e rotas críticas via TestClient.

Executar a partir da raiz do repositório:
  python tools/check_functionality.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    backend = root / "backend"
    os.chdir(backend)
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))

    # ``test`` evita echo SQL no engine; seed exige palavra-passe explícita fora de ``development``.
    os.environ.setdefault("APP_ENV", "test")
    os.environ.setdefault("BOOTSTRAP_SUPERADMIN_PASSWORD", "ChangeMe!SuperAdmin")
    fd, dbpath = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{dbpath}"

    from cryptography.fernet import Fernet

    os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())

    try:
        from fastapi.testclient import TestClient

        from app.main import app

        failures: list[str] = []

        def check(name: str, cond: bool, detail: str = "") -> None:
            if not cond:
                failures.append(f"{name}: {detail}".strip())

        with TestClient(app) as client:
            h = client.get("/health")
            check("GET /health", h.status_code == 200, h.text)
            if h.status_code == 200:
                check("GET /health body", h.json().get("status") == "ok", repr(h.json()))

            login = client.post(
                "/api/auth/login",
                json={"username": "superadmin", "password": "ChangeMe!SuperAdmin"},
            )
            check("POST /api/auth/login", login.status_code == 200, login.text)
            if login.status_code != 200:
                print("\n".join(failures), file=sys.stderr)
                return 1

            token = login.json().get("access_token")
            check("login token", bool(token), login.text)
            auth = {"Authorization": f"Bearer {token}"}

            me = client.get("/api/auth/me", headers=auth)
            check("GET /api/auth/me", me.status_code == 200, me.text)
            if me.status_code == 200:
                body = me.json()
                check("me.permissions", isinstance(body.get("permissions"), list), repr(body))

            oa = client.get("/openapi.json")
            check("GET /openapi.json", oa.status_code == 200, oa.text[:200])

            companies = client.get("/api/companies", headers=auth)
            check("GET /api/companies", companies.status_code == 200, companies.text)

            devices = client.get("/api/devices", headers=auth)
            check("GET /api/devices", devices.status_code == 200, devices.text)

        if failures:
            print("Falhas:", file=sys.stderr)
            for f in failures:
                print(f"  - {f}", file=sys.stderr)
            return 1

        print("check_functionality: OK (health, login, me, openapi, companies, devices)")
        return 0
    finally:
        try:
            os.unlink(dbpath)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
