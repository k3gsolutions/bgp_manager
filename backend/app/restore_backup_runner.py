from __future__ import annotations

import asyncio
import json

from sqlalchemy import insert

import app.models  # noqa: F401 - garante metadata completa
from app.database import AsyncSessionLocal, Base
from app.routers.management import _coerce_value, _resync_postgresql_sequences

BACKUP_PATH = "/tmp/restore-backup.json"


async def main() -> None:
    with open(BACKUP_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    payload = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(payload, dict):
        raise RuntimeError("Backup inválido: chave data ausente.")

    expected = raw.get("table_counts", {})

    table_by_name = {t.name: t for t in Base.metadata.sorted_tables}
    present_tables = [t for t in Base.metadata.sorted_tables if t.name in payload]
    unknown = sorted(k for k in payload.keys() if k not in table_by_name)

    print(f"tables_in_backup={len(payload)} known_tables={len(present_tables)}")
    if unknown:
        print("unknown_tables:", ",".join(unknown))

    async with AsyncSessionLocal() as db:
        async with db.begin_nested():
            for table in reversed(present_tables):
                await db.execute(table.delete())

            for table in present_tables:
                rows = payload.get(table.name) or []
                if not rows:
                    print(f"{table.name}: imported=0 expected={expected.get(table.name)}")
                    continue
                cooked = []
                for row in rows:
                    cooked.append(
                        {
                            col.name: _coerce_value(row.get(col.name), col)
                            for col in table.columns
                            if col.name in row
                        }
                    )
                try:
                    await db.execute(insert(table), cooked)
                except Exception as e:
                    print(f"ERROR table={table.name} rows={len(rows)} err={e!s}")
                    raise
                print(f"{table.name}: imported={len(rows)} expected={expected.get(table.name)}")

        await _resync_postgresql_sequences(db)
        await db.commit()

    print("restore_ok")


if __name__ == "__main__":
    asyncio.run(main())

