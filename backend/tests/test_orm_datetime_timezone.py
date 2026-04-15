"""
Garante que colunas DateTime do ORM usam timezone=True (compatível com PostgreSQL TIMESTAMPTZ).

SQLite em testes aceita naive e aware; PostgreSQL com TIMESTAMP sem TZ quebra em flush/insert.
Integração real com PostgreSQL: exporte DATABASE_URL=postgresql+asyncpg://... e rode pytest.
"""

from __future__ import annotations

from sqlalchemy import DateTime

from app import models as _models  # noqa: F401 — registra mappers no metadata
from app.database import Base


def test_all_mapped_datetime_columns_use_timezone_true() -> None:
    offenders: list[str] = []
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        table = getattr(cls, "__table__", None)
        if table is None:
            continue
        for col in table.columns:
            if isinstance(col.type, DateTime) and col.type.timezone is not True:
                offenders.append(f"{cls.__name__}.{col.key}")
    assert not offenders, "DateTime columns must use timezone=True: " + ", ".join(offenders)
