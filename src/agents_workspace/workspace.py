"""One root for every agents domain.

The root is ``AGENTS_WORKSPACE_ROOT`` (legacy: ``AGENTS_HOME``), else
``~/.agents``. Every domain resolves from it, with an explicit env override
per domain. Nothing here touches the network or starts a process.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

ENV_ROOT = "AGENTS_WORKSPACE_ROOT"
ENV_ROOT_LEGACY = "AGENTS_HOME"
ENV_WORKDIR = "AGENTS_WORKDIR"
ENV_WORKDIR_LEGACY = "AGENTS_WORKSPACE_DIR"

DIR_DOMAINS = (
    "memory",
    "rules",
    "skills",
    "modules",
    "schedules",
    "traces",
    "routine-runs",
    "relay",
    "personas",
)

DOMAIN_ENV = {
    "memory": "AGENTS_MEMORY_PATH",
    "skills": "AGENTS_SKILLS_DIR",
    "modules": "AGENTS_MODULES_DIR",
    "schedules": "AGENTS_SCHEDULES_DIR",
    "traces": "AGENTS_TRACES_DIR",
    "routine-runs": "AGENTS_ROUTINE_RUNS_DIR",
    "relay": "AGENTS_RELAY_STATE",
    "personas": "AGENTS_PERSONAS_DIR",
}

FILE_DOMAINS = ("identity", "config", "secrets", "store", "workspace")

ALL_DOMAINS = DIR_DOMAINS + ("claude",) + FILE_DOMAINS


def legacy_root() -> Path:
    """The pre-workspace root. Kept as a function so tests can patch it."""
    return (Path.home() / ".agents").resolve()


def workspaces_dir() -> Path:
    """Where additional workspaces live. Sibling of the default root."""
    raw = os.environ.get("AGENTS_WORKSPACES_DIR", "").strip()
    return _path(raw) if raw else (Path.home() / ".agents-workspaces").resolve()


def _clean(env: Mapping[str, str], name: str) -> str:
    return str(env.get(name) or "").strip()


def _path(raw: str) -> Path:
    return Path(raw).expanduser().resolve()


def _read_meta(root: Path) -> dict:
    path = root / "workspace.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


@dataclass(frozen=True)
class Workspace:
    root: Path
    workdir: Path
    id: str = ""
    label: str = ""
    default_persona: str = "default"
    explicit_root: bool = False
    source_env: Mapping[str, str] = field(default_factory=dict, repr=False, compare=False)

    # --- construction --------------------------------------------------

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Workspace":
        env = dict(os.environ if env is None else env)
        raw_root = _clean(env, ENV_ROOT) or _clean(env, ENV_ROOT_LEGACY)
        explicit = bool(raw_root)
        root = _path(raw_root) if raw_root else legacy_root()
        raw_work = _clean(env, ENV_WORKDIR) or _clean(env, ENV_WORKDIR_LEGACY)
        workdir = _path(raw_work) if raw_work else root / "workspace"
        meta = _read_meta(root)
        return cls(
            root=root,
            workdir=workdir,
            id=str(meta.get("id") or ""),
            label=str(meta.get("label") or ""),
            default_persona=str(meta.get("default_persona") or "default"),
            explicit_root=explicit,
            source_env=env,
        )

    @classmethod
    def list_all(cls, env: Mapping[str, str] | None = None) -> list["Workspace"]:
        """Default workspace plus every registered workspace on this host."""
        default = cls.from_env(env)
        items = [default]
        base = workspaces_dir()
        if base.is_dir():
            for child in sorted(base.iterdir()):
                if not (child / "workspace.json").is_file():
                    continue
                ws = cls.open(child, env=env)
                if ws.root != default.root:
                    items.append(ws)
        return items

    @classmethod
    def resolve(cls, ref: str, env: Mapping[str, str] | None = None) -> "Workspace | None":
        """Resolve a workspace by id, label, directory name, or path."""
        needle = (ref or "").strip()
        if not needle:
            return cls.from_env(env)
        for ws in cls.list_all(env):
            if needle in (ws.id, ws.label, ws.root.name):
                return ws
        candidate = Path(needle).expanduser()
        if candidate.is_dir() and (candidate / "workspace.json").is_file():
            return cls.open(candidate, env=env)
        return None

    @classmethod
    def create(cls, label: str, *, env: Mapping[str, str] | None = None) -> "Workspace":
        """Create a new workspace under ``workspaces_dir()`` and init it."""
        raw = (label or "workspace").strip().lower()
        slug = re.sub(r"[^a-z0-9-]+", "-", raw).strip("-") or "workspace"
        base = workspaces_dir()
        base.mkdir(parents=True, exist_ok=True)
        root = base / slug
        n = 2
        while root.exists():
            root = base / f"{slug}-{n}"
            n += 1
        return cls.open(root, env=env).init(label=label or slug)

    @classmethod
    def open(
        cls,
        root: str | Path,
        *,
        workdir: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "Workspace":
        env = dict(os.environ if env is None else env)
        root_p = _path(str(root))
        meta = _read_meta(root_p)
        return cls(
            root=root_p,
            workdir=_path(str(workdir)) if workdir else root_p / "workspace",
            id=str(meta.get("id") or ""),
            label=str(meta.get("label") or ""),
            default_persona=str(meta.get("default_persona") or "default"),
            explicit_root=True,
            source_env=env,
        )

    # --- domains -------------------------------------------------------

    def _domain(self, name: str) -> Path:
        env_name = DOMAIN_ENV.get(name)
        if env_name:
            raw = _clean(self.source_env, env_name)
            if raw:
                return _path(raw)
        return self.root / name

    @property
    def memory(self) -> Path:
        return self._domain("memory")

    @property
    def rules(self) -> Path:
        return self._domain("rules")

    @property
    def skills(self) -> Path:
        return self._domain("skills")

    @property
    def modules(self) -> Path:
        return self._domain("modules")

    @property
    def schedules(self) -> Path:
        return self._domain("schedules")

    @property
    def traces(self) -> Path:
        return self._domain("traces")

    @property
    def routine_runs(self) -> Path:
        return self._domain("routine-runs")

    @property
    def relay_state(self) -> Path:
        return self._domain("relay")

    @property
    def personas(self) -> Path:
        return self._domain("personas")

    @property
    def claude(self) -> Path:
        if self.explicit_root:
            return self.root / "claude"
        return Path.home() / ".claude"

    @property
    def identity(self) -> Path:
        raw = _clean(self.source_env, "AGENTS_IDENTITY_PATH")
        return _path(raw) if raw else self.root / "identity.json"

    @property
    def config(self) -> Path:
        return self.root / "config.json"

    @property
    def secrets(self) -> Path:
        return self.root / "secrets.json"

    @property
    def store(self) -> Path:
        return self.root / "store.db"

    @property
    def workspace_file(self) -> Path:
        return self.root / "workspace.json"

    def domain(self, name: str) -> Path:
        key = (name or "").strip().lower()
        if key in DIR_DOMAINS:
            return self._domain(key)
        if key == "claude":
            return self.claude
        if key == "identity":
            return self.identity
        if key == "config":
            return self.config
        if key == "secrets":
            return self.secrets
        if key == "store":
            return self.store
        if key == "workspace":
            return self.workspace_file
        raise KeyError(f"unknown workspace domain: {name}")

    # --- lifecycle -----------------------------------------------------

    def ensure(self) -> "Workspace":
        self.root.mkdir(parents=True, exist_ok=True)
        for name in DIR_DOMAINS:
            self._domain(name).mkdir(parents=True, exist_ok=True)
        return self

    def init(self, *, label: str = "", default_persona: str = "") -> "Workspace":
        self.ensure()
        meta = {
            "id": self.id or f"ws_{secrets.token_hex(8)}",
            "label": label or self.label or self.root.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "default_persona": default_persona or self.default_persona or "default",
        }
        self.workspace_file.write_text(
            json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return replace(
            self,
            id=meta["id"],
            label=meta["label"],
            default_persona=meta["default_persona"],
        )

    def migrate(self, *, dry_run: bool = False) -> list[tuple[Path, Path]]:
        """Move legacy ``~/.agents/<domain>`` dirs into this workspace.

        Only when the workspace root is not the legacy root and the target does
        not exist yet. Returns the planned or performed moves.
        """
        source_root = legacy_root()
        moves: list[tuple[Path, Path]] = []
        if self.root == source_root:
            return moves
        for name in DIR_DOMAINS:
            src = source_root / name
            dst = self._domain(name)
            if not src.is_dir() or dst.exists():
                continue
            moves.append((src, dst))
            if dry_run:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        return moves

    def env(self, base: Mapping[str, str] | None = None) -> dict[str, str]:
        """Process env for a turn or job that must run inside this workspace."""
        out = dict(os.environ if base is None else base)
        out["AGENTS_WORKSPACE_ROOT"] = str(self.root)
        out["AGENTS_WORKDIR"] = str(self.workdir)
        out["AGENTS_MEMORY_PATH"] = str(self.memory)
        out["AGENTS_TRACES_DIR"] = str(self.traces)
        out["AGENTS_SCHEDULES_DIR"] = str(self.schedules)
        out["AGENTS_ROUTINE_RUNS_DIR"] = str(self.routine_runs)
        out["AGENTS_PERSONAS_DIR"] = str(self.personas)
        out["AGENTS_SKILLS_DIR"] = str(self.skills)
        out["AGENTS_MODULES_DIR"] = str(self.modules)
        out["AGENTS_IDENTITY_PATH"] = str(self.identity)
        return out

    # --- reporting -----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "root": str(self.root),
            "workdir": str(self.workdir),
            "id": self.id,
            "label": self.label,
            "default_persona": self.default_persona,
            "domains": {name: str(self._domain(name)) for name in DIR_DOMAINS},
            "claude": str(self.claude),
            "identity": str(self.identity),
            "config": str(self.config),
            "secrets": str(self.secrets),
            "store": str(self.store),
        }


def current() -> Workspace:
    """The workspace for this process."""
    return Workspace.from_env()
