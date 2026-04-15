"""
Ambiente isolado para pytest antes de importar ``app`` (DATABASE_URL, APP_ENV, chaves).
"""

from __future__ import annotations

import atexit
import os
import sys
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

_backend = Path(__file__).resolve().parents[1]
os.chdir(_backend)
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

_fd, _dbpath = tempfile.mkstemp(suffix=".db")
os.close(_fd)


def _cleanup_db() -> None:
    try:
        os.unlink(_dbpath)
    except OSError:
        pass


atexit.register(_cleanup_db)

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_dbpath}"
# ``test`` como em ``tools/check_functionality.py`` — antes só ``development`` activava regex CORS LAN.
os.environ["APP_ENV"] = "test"
os.environ["BOOTSTRAP_SUPERADMIN_PASSWORD"] = "ChangeMe!SuperAdmin"
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-at-least-32-characters-long!")
os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())
