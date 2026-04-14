from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
import threading
from typing import Callable

from ..config import settings

_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_semver(tag: str) -> tuple[int, int, int] | None:
    m = _SEMVER_RE.match((tag or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _version_cmp(a: str, b: str) -> int | None:
    pa = _parse_semver(a)
    pb = _parse_semver(b)
    if pa is None or pb is None:
        return None
    if pa < pb:
        return -1
    if pa > pb:
        return 1
    return 0


@dataclass
class UpdateState:
    current_version: str = "unknown"
    latest_version: str | None = None
    latest_source: str | None = None
    status: str = "idle"  # idle | checking | up_to_date | update_available | running | success | error
    update_available: bool = False
    last_checked_at: str | None = None
    last_run_started_at: str | None = None
    last_run_finished_at: str | None = None
    error: str | None = None
    running: bool = False
    restart_required: bool = False
    logs: list[str] = field(default_factory=list)


class SystemUpdateService:
    def __init__(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[3]
        self.backend_dir = self.repo_root / "backend"
        self.frontend_dir = self.repo_root / "frontend"
        self._state = UpdateState()
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._refresh_current_version_locked()

    def _append_log(self, msg: str) -> None:
        self._state.logs.append(f"[{_now_iso()}] {msg}")
        if len(self._state.logs) > 300:
            self._state.logs = self._state.logs[-300:]

    def _run(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        timeout: int = 180,
        on_output: Callable[[str], None] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        cp = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if on_output:
            if cp.stdout.strip():
                on_output(cp.stdout.strip())
            if cp.stderr.strip():
                on_output(cp.stderr.strip())
        return cp

    def _git(self, *args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
        return self._run(["git", *args], cwd=self.repo_root, timeout=timeout)

    def _refresh_current_version_locked(self) -> None:
        cp_tag = self._git("describe", "--tags", "--abbrev=0")
        cp_sha = self._git("rev-parse", "--short", "HEAD")
        tag = cp_tag.stdout.strip() if cp_tag.returncode == 0 else None
        sha = cp_sha.stdout.strip() if cp_sha.returncode == 0 else "unknown"
        self._state.current_version = f"{tag} ({sha})" if tag else sha

    def _remote_latest(self) -> tuple[str | None, str | None, str | None]:
        cp_tags = self._git("ls-remote", "--tags", "--refs", "origin")
        if cp_tags.returncode == 0:
            tags: list[str] = []
            for line in cp_tags.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) != 2:
                    continue
                ref = parts[1]
                if ref.startswith("refs/tags/"):
                    tags.append(ref.replace("refs/tags/", "", 1))
            semver_tags = [t for t in tags if _parse_semver(t)]
            if semver_tags:
                semver_tags.sort(key=lambda x: _parse_semver(x) or (0, 0, 0))
                latest = semver_tags[-1]
                return latest, "tag", None

        cp_head = self._git("ls-remote", "--heads", "origin", "main")
        if cp_head.returncode != 0 or not cp_head.stdout.strip():
            err = cp_head.stderr.strip() or cp_tags.stderr.strip() or "Falha ao consultar origin."
            return None, None, err
        sha = cp_head.stdout.split()[0][:12]
        return sha, "commit", None

    def check(self) -> dict:
        with self._lock:
            self._state.status = "checking"
            self._state.error = None
            self._append_log("Verificando versões no repositório remoto...")
            self._refresh_current_version_locked()

            latest, source, err = self._remote_latest()
            self._state.last_checked_at = _now_iso()
            self._state.latest_version = latest
            self._state.latest_source = source

            if err:
                self._state.status = "error"
                self._state.error = err
                self._state.update_available = False
                self._append_log(f"Falha na verificação: {err}")
                return asdict(self._state)

            cur = self._state.current_version.split(" ")[0]
            if source == "tag":
                cmpv = _version_cmp(cur, latest or "")
                if cmpv is not None and cmpv < 0:
                    self._state.status = "update_available"
                    self._state.update_available = True
                else:
                    self._state.status = "up_to_date"
                    self._state.update_available = False
            else:
                self._state.status = "update_available" if (latest and latest not in self._state.current_version) else "up_to_date"
                self._state.update_available = self._state.status == "update_available"

            self._append_log(
                f"Versão atual: {self._state.current_version} | mais recente: {latest or 'n/a'} ({source or 'desconhecido'})"
            )
            return asdict(self._state)

    def status(self) -> dict:
        with self._lock:
            self._refresh_current_version_locked()
            return asdict(self._state)

    def _run_step(self, label: str, cmd: list[str], cwd: Path, timeout: int = 900) -> None:
        self._append_log(f"[{label}] executando: {' '.join(cmd)}")
        try:
            cp = self._run(cmd, cwd=cwd, timeout=timeout, on_output=self._append_log)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"{label}: timeout após {timeout}s") from e
        if cp.returncode != 0:
            raise RuntimeError(f"{label}: falhou (exit {cp.returncode})")
        self._append_log(f"[{label}] concluído")

    def _update_worker(self, actor: str) -> None:
        start_head = ""
        try:
            with self._lock:
                self._state.running = True
                self._state.status = "running"
                self._state.error = None
                self._state.restart_required = False
                self._state.last_run_started_at = _now_iso()
                self._append_log(f"Atualização iniciada por {actor}")

            cp_head = self._git("rev-parse", "HEAD")
            if cp_head.returncode == 0:
                start_head = cp_head.stdout.strip()

            cp_dirty = self._git("status", "--porcelain")
            if cp_dirty.returncode != 0:
                raise RuntimeError(cp_dirty.stderr.strip() or "Falha ao verificar estado do git.")
            if cp_dirty.stdout.strip():
                raise RuntimeError("Repositório local possui alterações pendentes. Commit/stash antes de atualizar.")

            self._run_step("git-fetch", ["git", "fetch", "--tags", "origin"], self.repo_root, timeout=180)
            self._run_step("git-pull", ["git", "pull", "--ff-only", "origin", "main"], self.repo_root, timeout=240)

            venv_python = self.backend_dir / ".venv" / "bin" / "python"
            if not venv_python.exists():
                self._run_step("venv-create", ["python3", "-m", "venv", ".venv"], self.backend_dir, timeout=180)
            self._run_step(
                "backend-deps",
                [str(venv_python), "-m", "pip", "install", "-r", "requirements.txt"],
                self.backend_dir,
                timeout=900,
            )
            self._run_step("frontend-deps", ["npm", "install"], self.frontend_dir, timeout=900)
            self._run_step("frontend-build", ["npm", "run", "build"], self.frontend_dir, timeout=900)

            alembic_ini = self.backend_dir / "alembic.ini"
            if alembic_ini.exists():
                self._run_step("db-migrate", [str(venv_python), "-m", "alembic", "upgrade", "head"], self.backend_dir, timeout=300)
            else:
                with self._lock:
                    self._append_log("[db-migrate] alembic.ini não encontrado, etapa ignorada.")

            restart_cmds = []
            if (settings.update_backend_restart_cmd or "").strip():
                restart_cmds.append(("backend-restart", settings.update_backend_restart_cmd.strip()))
            if (settings.update_frontend_restart_cmd or "").strip():
                restart_cmds.append(("frontend-restart", settings.update_frontend_restart_cmd.strip()))

            if restart_cmds:
                for lbl, cmd in restart_cmds:
                    self._run_step(lbl, ["bash", "-lc", cmd], self.repo_root, timeout=180)
            else:
                with self._lock:
                    self._state.restart_required = True
                    self._append_log("Nenhum comando automático de restart configurado. Reinicie serviços manualmente.")

            with self._lock:
                self._refresh_current_version_locked()
                self._state.status = "success"
                self._state.update_available = False
                self._append_log("Atualização concluída com sucesso.")
        except Exception as e:
            with self._lock:
                self._state.error = str(e)
                self._append_log(f"Erro na atualização: {e!s}")
            if start_head:
                cp_rb = self._git("reset", "--hard", start_head)
                with self._lock:
                    if cp_rb.returncode == 0:
                        self._append_log(f"Rollback aplicado para commit {start_head[:12]}.")
                    else:
                        self._append_log(f"Rollback falhou: {cp_rb.stderr.strip() or 'erro desconhecido'}.")
            with self._lock:
                self._state.status = "error"
        finally:
            with self._lock:
                self._state.running = False
                self._state.last_run_finished_at = _now_iso()

    def start_update(self, actor: str) -> dict:
        with self._lock:
            if self._state.running:
                raise RuntimeError("Já existe uma atualização em execução.")
            self._worker = threading.Thread(target=self._update_worker, args=(actor,), daemon=True)
            self._worker.start()
            return asdict(self._state)


system_update_service = SystemUpdateService()
