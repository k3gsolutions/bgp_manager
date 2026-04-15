"""Export/import de backup completo (Gerenciamento) — regressão para migração entre servidores."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


def _login(client: TestClient) -> str:
    pwd = (os.environ.get("BOOTSTRAP_SUPERADMIN_PASSWORD") or "ChangeMe!SuperAdmin").strip()
    r = client.post(
        "/api/auth/login",
        json={"username": "superadmin", "password": pwd},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_backup_export_import_roundtrip(client: TestClient) -> None:
    token = _login(client)
    headers = {"Authorization": f"Bearer {token}"}
    r = client.get("/api/management/backup/export", headers=headers)
    assert r.status_code == 200, r.text
    payload = r.json()
    assert "data" in payload and isinstance(payload["data"], dict)
    assert "companies" in payload["data"] and isinstance(payload["data"]["companies"], list)

    imp = client.post(
        "/api/management/backup/import",
        headers=headers,
        json={"data": payload["data"]},
    )
    assert imp.status_code == 200, imp.text
    body = imp.json()
    assert "table_counts" in body


def test_coerce_datetime_naive_string_for_tz_column() -> None:
    from sqlalchemy import Column, DateTime, Integer, Table, MetaData

    from app.routers.management import _coerce_value

    md = MetaData()
    t = Table("t", md, Column("id", Integer), Column("ts", DateTime(timezone=True)))
    col = t.c.ts
    out = _coerce_value("2024-01-15T12:00:00", col)
    assert out.tzinfo is not None
    assert out.hour == 12
