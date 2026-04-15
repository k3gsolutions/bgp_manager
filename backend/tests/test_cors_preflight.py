"""
Reproduz falha de CORS quando o frontend usa VITE_API_URL (axios → :8000) e o Vite
abre por IP da LAN (Origin http://192.168.x.x:5174): o preflight OPTIONS deve devolver
Access-Control-Allow-Origin com esse host.

Antes da correcção, só ``APP_ENV=development`` activava ``allow_origin_regex``; com
``APP_ENV=test`` (CI / checks) o preflight falhava — o navegador reporta como «sem resposta».
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


def test_cors_preflight_lan_vite_origin_with_absolute_api_url_scenario(client: TestClient) -> None:
    """
    Simula: Vite em http://192.168.1.50:5174 + API em http://127.0.0.1:8000 (VITE_API_URL).
    O browser envia Origin da página (LAN), não a do API.
    """
    origin = "http://192.168.1.50:5174"
    resp = client.options(
        "/api/auth/login",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.status_code == 200, resp.text
    allow = resp.headers.get("access-control-allow-origin")
    assert allow == origin, f"esperado ACAO={origin!r}, obtido {allow!r} — headers={dict(resp.headers)}"


def test_cors_get_health_with_lan_origin(client: TestClient) -> None:
    origin = "http://10.20.30.40:5174"
    r = client.get("/health", headers={"Origin": origin})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == origin


def test_cors_preflight_localhost_vite_port_other_than_5174(client: TestClient) -> None:
    """Regex cobre qualquer porta em 127.0.0.1 (ex.: Vite noutra porta)."""
    origin = "http://127.0.0.1:5999"
    resp = client.options(
        "/api/auth/me",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == origin
