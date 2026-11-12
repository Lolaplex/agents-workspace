"""Host > Workspace > Persona > Session resolver.

The Host (``AGENTS_HOST_DIR``, default ``~/.agents``) holds infra only:
keys, secrets, run, cache, logs, clients, agents.json, users.json.
Every Workspace lives at ``<host>/workspaces/<id>`` and holds the domains
(memory, skills, modules, schedules, traces, routine-runs, relay, personas,
rules, content, work). A workspace is selected per call by id
(``AGENTS_WORKSPACE=<id>``), never by a process-global root.

Deprecated aliases (one release, then removed): ``AGENTS_HOME`` ->
``AGENTS_HOST_DIR``, ``AGENTS_WORKSPACE_DIR`` -> ``AGENTS_WORKDIR``,
``AGENTS_IDENTITY_PATH`` -> ``AGENTS_USERS_FILE``,
``AGENTS_WORKSPACE_ROOT`` -> ad-hoc workspace dir,
``AGENTS_WORKSPACES_DIR`` -> ``<host>/workspaces``.

Nothing here touches the network or starts a process.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import warnings
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

ENV_HOST_DIR = "AGENTS_HOST_DIR"
ENV_HOST_LEGACY = "AGENTS_HOME"  # deprecated alias, one release
ENV_WORKSPACE = "AGENTS_WORKSPACE"  # workspace id, not a path
ENV_WORKDIR = "AGENTS_WORKDIR"
ENV_WORKDIR_LEGACY = "AGENTS_WORKSPACE_DIR"  # deprecated alias, one release
ENV_USERS_FILE = "AGENTS_USERS_FILE"  # -> <host>/users.json
ENV_USERS_FILE_LEGACY = "AGENTS_IDENTITY_PATH"  # deprecated alias, one release
ENV_WORKSPACES_DIR = "AGENTS_WORKSPACES_DIR"  # deprecated: <host>/workspaces
ENV_ROOT = "AGENTS_WORKSPACE_ROOT"  # deprecated: ad-hoc workspace dir
ENV_WORKSPACE_ID_LEGACY = "AGENTS_WORKSPACE_ID"

# Env vars removed by the Host/Workspace split. Workspace.env() strips them
# from child envs so a parent can never leak them into a turn.
REMOVED_ENV = ("AGENTS_DIR",)

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
    "content",
    "work",
    "browser",
)

FILE_DOMAINS = (
    "config",
    "secrets",
    "store",
    "workspace",
    "members",
    "sync_config",
    "mcp",
    "calendar",
)

ALL_DOMAINS = DIR_DOMAINS + ("claude",) + FILE_DOMAINS + ("identity",)

# Domain overrides are test/debug only: they apply to the workspace chosen at
# CLI entry (Workspace.from_env) and are ignored everywhere else (C11).
DOMAIN_ENV = {
    "memory": "AGENTS_MEMORY_DIR",
    "skills": "AGENTS_SKILLS_DIR",
    "modules": "AGENTS_MODULES_DIR",
    "schedules": "AGENTS_SCHEDULES_DIR",
    "traces": "AGENTS_TRACES_DIR",
    "routine-runs": "AGENTS_ROUTINE_RUNS_DIR",
    "relay": "AGENTS_RELAY_DIR",
    "personas": "AGENTS_PERSONAS_DIR",
    "rules": "AGENTS_RULES_DIR",
}

# Deprecated aliases for DOMAIN_ENV names (one release, then removed).
DOMAIN_ENV_ALIASES = {
    "memory": "AGENTS_MEMORY_PATH",
    "relay": "AGENTS_RELAY_STATE",
}

# Every env var the resolver owns: ``Workspace.env()`` pops all of them from
# the child env first, then sets this workspace's values (C12).
_ENV_KEYS = frozenset(
    (
        ENV_HOST_DIR,
        ENV_HOST_LEGACY,
        ENV_WORKSPACE,
        ENV_WORKDIR,
        ENV_WORKDIR_LEGACY,
        ENV_USERS_FILE,
        ENV_USERS_FILE_LEGACY,
        ENV_WORKSPACES_DIR,
        ENV_ROOT,
        ENV_WORKSPACE_ID_LEGACY,
        "AGENTS_PERSONA",
        "AGENTS_KEYS_DIR",
        "AGENTS_MEMORY_DIR",
        "AGENTS_RELAY_DIR",
        "AGENTS_CONTENT_DIR",
        "AGENTS_BROWSER_PROFILE",
        "AGENTS_BROWSER_SESSION",
        "AGENTS_KNOWN_DIDS_FILE",
        "AGENTS_KNOWN_DIDS",
        "AGENTS_MCP_CONFIG_FILE",
        "AGENTS_MCP_CONFIG",
        "AGENTS_MCP_CACHE_FILE",
        "AGENTS_MCP_CACHE",
        "AGENTS_SYNC_CONFIG_FILE",
        "AGENTS_SYNC_CONFIG",
        *REMOVED_ENV,
        *DOMAIN_ENV.values(),
        *DOMAIN_ENV_ALIASES.values(),
    )
)

_ID_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _clean(env: Mapping[str, str], name: str) -> str:
    return str(env.get(name) or "").strip()


def _path(raw: str) -> Path:
    return Path(raw).expanduser().resolve()


def _deprecated(old: str, new: str) -> None:
    warnings.warn(
        f"{old} is deprecated; use {new} (alias removed after one release)",
        DeprecationWarning,
        stacklevel=3,
    )


def _slug(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", (raw or "").strip().lower()).strip("-")
    return slug or "workspace"


def _valid_id(ws_id: str) -> str:
    ws_id = (ws_id or "").strip()
    if not ws_id or not _ID_OK.match(ws_id) or ".." in ws_id:
        raise ValueError(f"invalid workspace id: {ws_id!r}")
    return ws_id


def default_host_dir() -> Path:
    return (Path.home() / ".agents").resolve()


def host_dir(env: Mapping[str, str] | None = None) -> Path:
    """Resolve the Host dir: AGENTS_HOST_DIR > AGENTS_HOME (deprecated) > ~/.agents."""
    env = os.environ if env is None else env
    raw = _clean(env, ENV_HOST_DIR)
    if raw:
        return _path(raw)
    legacy = _clean(env, ENV_HOST_LEGACY)
    if legacy:
        _deprecated(ENV_HOST_LEGACY, ENV_HOST_DIR)
        return _path(legacy)
    return default_host_dir()


def _is_canonical(ws: "Workspace | None", domain: str, raw: str) -> bool:
    """Override that just re-states the resolved path is not an override."""
    if ws is None:
        return False
    # ws.dir / <domain> is the canonical layout; ws.<attr> would already carry
    # the override when apply_overrides is set on the entry workspace.
    return _path(raw) == ws.dir / domain


def _is_canonical_users_file(ws: "Workspace | None", raw: str) -> bool:
    if ws is None:
        return False
    return _path(raw) == ws.host.dir / "users.json"


def _warn_domain_overrides(
    env: Mapping[str, str], ws: "Workspace | None" = None
) -> None:
    """Domain overrides are test/debug only (D-domain-overrides)."""
    names = [
        var
        for domain, var in DOMAIN_ENV.items()
        if _clean(env, var) and not _is_canonical(ws, domain, env[var])
    ]
    names += [
        var
        for domain, var in DOMAIN_ENV_ALIASES.items()
        if _clean(env, var) and not _is_canonical(ws, domain, env[var])
    ]
    for extra in (ENV_USERS_FILE, ENV_USERS_FILE_LEGACY):
        raw = _clean(env, extra)
        if raw and not _is_canonical_users_file(ws, raw):
            names.append(extra)
    if names:
        warnings.warn(
            "domain env overrides are test/debug only and apply only to the "
            "workspace chosen at CLI entry: " + ", ".join(sorted(names)),
            UserWarning,
            stacklevel=3,
        )


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
class Host:
    """Infra level: keys, secrets, run, cache, logs, clients. No memory/skills."""

    dir: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Host":
        return cls(dir=host_dir(env))

    @classmethod
    def open(cls, dir: str | Path) -> "Host":
        return cls(dir=_path(str(dir)))

    # --- infra paths ---------------------------------------------------

    @property
    def workspaces_dir(self) -> Path:
        return self.dir / "workspaces"

    @property
    def keys(self) -> Path:
        return self.dir / "keys"

    @property
    def secrets(self) -> Path:
        return self.dir / "secrets.json"

    @property
    def run(self) -> Path:
        return self.dir / "run"

    @property
    def cache(self) -> Path:
        return self.dir / "cache"

    @property
    def logs(self) -> Path:
        return self.dir / "logs"

    @property
    def clients(self) -> Path:
        return self.dir / "clients"

    @property
    def browser(self) -> Path:
        return self.dir / "browser"

    @property
    def agents_json(self) -> Path:
        return self.dir / "agents.json"

    @property
    def mcp_json(self) -> Path:
        return self.dir / "mcp.json"

    @property
    def host_paths(self) -> Path:
        return self.dir / "host_paths.json"

    def has_legacy_layout(self) -> bool:
        """Host was never migrated: no workspaces/, legacy domains on top."""
        return Workspace._is_legacy_root(self.dir)

    def users_file(self, env: Mapping[str, str] | None = None) -> Path:
        env = {} if env is None else env
        raw = _clean(env, ENV_USERS_FILE)
        if raw:
            return _path(raw)
        legacy = _clean(env, ENV_USERS_FILE_LEGACY)
        if legacy:
            _deprecated(ENV_USERS_FILE_LEGACY, ENV_USERS_FILE)
            return _path(legacy)
        return self.dir / "users.json"

    # --- workspaces ----------------------------------------------------

    def workspace(self, ws_id: str, *, env: Mapping[str, str] | None = None) -> "Workspace":
        """The workspace ``<host>/workspaces/<id>``; never reads domain overrides."""
        ws_id = _valid_id(ws_id)
        root = self._workspaces_dir(env) / ws_id
        meta = _read_meta(root)
        return Workspace(
            host=self,
            dir=root,
            workdir=root / "work",
            id=str(meta.get("id") or ws_id),
            label=str(meta.get("label") or ""),
            default_persona=str(meta.get("default_persona") or "default"),
            parent_workspace=str(meta.get("parent_workspace") or ""),
            isolation=str(meta.get("isolation") or ""),
            image=str(meta.get("image") or ""),
            source_env=dict(env or {}),
            apply_overrides=False,
        )

    def _workspaces_dir(self, env: Mapping[str, str] | None = None) -> Path:
        env = os.environ if env is None else env
        raw = _clean(env, ENV_WORKSPACES_DIR)
        if raw:
            _deprecated(ENV_WORKSPACES_DIR, f"{ENV_HOST_DIR}/workspaces")
            return _path(raw)
        return self.workspaces_dir

    def workspace_exists(
        self, ws_id: str, *, env: Mapping[str, str] | None = None
    ) -> bool:
        try:
            _valid_id(ws_id)
        except ValueError:
            return False
        return (self._workspaces_dir(env) / ws_id / "workspace.json").is_file()

    def list_workspaces(
        self, *, env: Mapping[str, str] | None = None
    ) -> list["Workspace"]:
        base = self._workspaces_dir(env)
        if not base.is_dir():
            return []
        out = []
        for child in sorted(base.iterdir()):
            if child.is_dir() and (child / "workspace.json").is_file():
                out.append(self.workspace(child.name, env=env))
        return out

    def create(
        self,
        ws_id: str,
        label: str = "",
        persona: str = "default",
        *,
        env: Mapping[str, str] | None = None,
    ) -> "Workspace":
        """Create ``<host>/workspaces/<id>`` plus its first persona."""
        ws = self.workspace(ws_id, env=env).init(
            label=label or ws_id, default_persona=persona or "default"
        )
        persona_dir = ws.personas / ws.default_persona
        persona_dir.mkdir(parents=True, exist_ok=True)
        pfile = persona_dir / "persona.json"
        if not pfile.is_file():
            pfile.write_text(
                json.dumps(
                    {
                        "id": ws.default_persona,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        return ws

    def select_workspace(
        self, ws_id: str | None = None, *, env: Mapping[str, str] | None = None
    ) -> "Workspace":
        """explicit id > AGENTS_WORKSPACE > the only existing workspace.

        First start (no workspaces yet) creates ``default``. Several existing
        workspaces without an id is an error (D-default-ws).
        """
        ws_id = (ws_id or "").strip()
        if not ws_id and env is not None:
            ws_id = _clean(env, ENV_WORKSPACE)
        if ws_id:
            if not self.workspace_exists(ws_id, env=env):
                raise ValueError(f"unknown workspace id: {ws_id!r}")
            return self.workspace(ws_id, env=env)
        found = self.list_workspaces(env=env)
        if not found:
            return self.create("default", env=env)
        if len(found) == 1:
            return found[0]
        raise ValueError(
            "several workspaces exist; pass a workspace id "
            f"({ENV_WORKSPACE} or --workspace)"
        )

    def delete(
        self,
        ws_id: str,
        *,
        hard: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> Path:
        """Archive a workspace to ``workspaces/.trash/<id>-<utc>`` (default)
        or remove it entirely (``hard=True``). Returns the archive path or
        the removed root. Refuses the env-selected workspace."""
        ws_id = _valid_id(ws_id)
        selected = _clean(env if env is not None else os.environ, ENV_WORKSPACE)
        if selected and selected == ws_id:
            raise ValueError(
                f"cannot delete the selected workspace {ws_id!r} "
                f"(unset {ENV_WORKSPACE} first)"
            )
        root = self._workspaces_dir(env) / ws_id
        if not (root / "workspace.json").is_file():
            raise ValueError(f"unknown workspace id: {ws_id!r}")
        if hard:
            shutil.rmtree(root)
            return root
        trash = self._workspaces_dir(env) / ".trash"
        trash.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        dst = trash / f"{ws_id}-{stamp}"
        shutil.move(str(root), str(dst))
        return dst


@dataclass(frozen=True)
class Workspace:
    """A data workspace under a Host. Resolve via ``Host.workspace(id)``."""

    host: Host
    dir: Path
    workdir: Path
    id: str = ""
    label: str = ""
    default_persona: str = "default"
    parent_workspace: str = ""  # reserved for Track E job workspaces
    isolation: str = ""  # manifest: local | container (runtime.py resolves)
    image: str = ""  # optional container image override
    source_env: Mapping[str, str] = field(
        default_factory=dict, repr=False, compare=False
    )
    apply_overrides: bool = field(default=False, repr=False, compare=False)

    # --- construction --------------------------------------------------

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Workspace":
        """CLI-entry selection. Only entry point allowed to read process env."""
        env = dict(os.environ if env is None else env)
        ad_hoc = _clean(env, ENV_ROOT)
        if ad_hoc:
            _deprecated(ENV_ROOT, f"{ENV_HOST_DIR} + {ENV_WORKSPACE}=<id>")
            _warn_domain_overrides(env)
            return cls._ad_hoc(ad_hoc, env)
        legacy_home = _clean(env, ENV_HOST_LEGACY)
        if legacy_home and not _clean(env, ENV_HOST_DIR):
            # AGENTS_HOME used to name the workspace root itself. Treat it as
            # a Host only once it actually looks like one (has workspaces/);
            # an old-style root resolves as an ad-hoc workspace instead.
            candidate = _path(legacy_home)
            if not (candidate / "workspaces").is_dir():
                _deprecated(ENV_HOST_LEGACY, f"{ENV_HOST_DIR} + {ENV_WORKSPACE}=<id>")
                return cls._ad_hoc(legacy_home, env)
        host = Host.from_env(env)
        if _clean(env, ENV_WORKSPACE) == "" and cls._is_legacy_root(host.dir):
            # Pre-migration host: legacy domain dirs live at the host level.
            # Keep resolving it as the workspace until `migrate` runs (C80).
            _deprecated(
                f"legacy host layout {host.dir}",
                f"{ENV_WORKSPACE}=<id> after agents-workspace migrate",
            )
            return cls._ad_hoc(str(host.dir), env)
        ws = host.select_workspace(env=env)
        workdir = cls._workdir_from_env(env, ws.dir)
        ws = replace(ws, workdir=workdir, source_env=env, apply_overrides=True)
        _warn_domain_overrides(env, ws)
        return ws

    @staticmethod
    @staticmethod
    def _is_legacy_root(host_dir: Path) -> bool:
        """A host that was never migrated: no workspaces/, legacy domains on top."""
        if (host_dir / "workspaces").is_dir():
            return False
        markers = (
            "memory",
            "skills",
            "modules",
            "schedules",
            "traces",
            "routine-runs",
            "relay",
            "personas",
            "workspace.json",
            "store.db",
            "identity.json",
            "config.json",
        )
        return any((host_dir / m).exists() for m in markers)

    @classmethod
    def _ad_hoc(cls, raw_dir: str, env: Mapping[str, str]) -> "Workspace":
        """Deprecated explicit-dir workspace (AGENTS_WORKSPACE_ROOT bridge)."""
        root = _path(raw_dir)
        meta = _read_meta(root)
        return cls(
            host=Host.from_env(env),
            dir=root,
            workdir=cls._workdir_from_env(env, root),
            id=str(meta.get("id") or root.name),
            label=str(meta.get("label") or ""),
            default_persona=str(meta.get("default_persona") or "default"),
            parent_workspace=str(meta.get("parent_workspace") or ""),
            isolation=str(meta.get("isolation") or ""),
            image=str(meta.get("image") or ""),
            source_env=dict(env),
            apply_overrides=True,
        )

    @staticmethod
    def _workdir_from_env(env: Mapping[str, str], ws_dir: Path) -> Path:
        raw = _clean(env, ENV_WORKDIR)
        if raw:
            return _path(raw)
        legacy = _clean(env, ENV_WORKDIR_LEGACY)
        if legacy:
            _deprecated(ENV_WORKDIR_LEGACY, ENV_WORKDIR)
            return _path(legacy)
        return ws_dir / "work"

    @classmethod
    def _ad_hoc_open(
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
            host=Host.from_env(env),
            dir=root_p,
            workdir=_path(str(workdir)) if workdir else root_p / "work",
            id=str(meta.get("id") or root_p.name),
            label=str(meta.get("label") or ""),
            default_persona=str(meta.get("default_persona") or "default"),
            parent_workspace=str(meta.get("parent_workspace") or ""),
            isolation=str(meta.get("isolation") or ""),
            image=str(meta.get("image") or ""),
            source_env=env,
            apply_overrides=False,
        )

    # --- deprecated constructors (compat, removed after one release) -----

    @classmethod
    def open(
        cls,
        root: str | Path,
        *,
        workdir: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "Workspace":
        """Deprecated: open an arbitrary dir as a workspace."""
        _deprecated("Workspace.open(dir)", "Host.workspace(id)")
        return cls._ad_hoc_open(root, workdir=workdir, env=env)

    @classmethod
    def create(cls, label: str, *, env: Mapping[str, str] | None = None) -> "Workspace":
        """Deprecated: create by label. Use ``Host.create(id, label)``."""
        _deprecated("Workspace.create(label)", "Host.create(id, label)")
        host = Host.from_env(env)
        return host.create(_slug(label), label=label or "workspace", env=env)

    @classmethod
    def list_all(cls, env: Mapping[str, str] | None = None) -> list["Workspace"]:
        """Every workspace on this host."""
        env = dict(os.environ if env is None else env)
        host = Host.from_env(env)
        items = host.list_workspaces(env=env)
        try:
            current_ws = cls.from_env(env)
        except ValueError:
            current_ws = None
        if current_ws is not None:
            # Swap in the env-selected instance so its overrides apply.
            for i, ws in enumerate(items):
                if ws.dir == current_ws.dir:
                    items[i] = current_ws
                    break
            else:
                items.insert(0, current_ws)
        return items

    @classmethod
    def resolve(cls, ref: str, env: Mapping[str, str] | None = None) -> "Workspace | None":
        """Resolve a workspace by id; legacy refs also match label/dir/path."""
        env = dict(os.environ if env is None else env)
        needle = (ref or "").strip()
        if not needle:
            try:
                return cls.from_env(env)
            except ValueError:
                return None
        host = Host.from_env(env)
        if host.workspace_exists(needle):
            return host.workspace(needle, env=env)
        for ws in cls.list_all(env):
            if needle in (ws.id, ws.label, ws.dir.name):
                return ws
        candidate = Path(needle).expanduser()
        if candidate.is_dir() and (candidate / "workspace.json").is_file():
            _deprecated("resolve by path", "resolve by id")
            return cls._ad_hoc_open(candidate, env=env)
        return None

    # --- deprecated fields ----------------------------------------------

    @property
    def root(self) -> Path:
        _deprecated("Workspace.root", "Workspace.dir")
        return self.dir

    @property
    def explicit_root(self) -> bool:
        _deprecated("Workspace.explicit_root", "Host/Workspace split")
        return True

    # --- domains -------------------------------------------------------

    def _domain(self, name: str) -> Path:
        if self.apply_overrides:
            env_name = DOMAIN_ENV.get(name)
            if env_name:
                raw = _clean(self.source_env, env_name)
                if raw:
                    return _path(raw)
                alias = DOMAIN_ENV_ALIASES.get(name)
                if alias:
                    raw = _clean(self.source_env, alias)
                    if raw:
                        _deprecated(alias, env_name)
                        return _path(raw)
        return self.dir / name

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
    def content(self) -> Path:
        return self.dir / "content"

    @property
    def work(self) -> Path:
        return self.dir / "work"

    @property
    def browser(self) -> Path:
        """Browser artifacts dir (the profile lives on the Host)."""
        return self.dir / "browser"

    @property
    def claude(self) -> Path:
        return Path.home() / ".claude"

    @property
    def identity(self) -> Path:
        return self.host.users_file(self.source_env)

    @property
    def members(self) -> Path:
        return self.dir / "members.json"

    @property
    def config(self) -> Path:
        return self.dir / "config.json"

    @property
    def secrets(self) -> Path:
        return self.dir / "secrets.json"

    @property
    def store(self) -> Path:
        return self.dir / "store.db"

    @property
    def sync_config(self) -> Path:
        return self.dir / "sync_config.json"

    @property
    def mcp_json(self) -> Path:
        return self.dir / "mcp.json"

    @property
    def calendar(self) -> Path:
        return self.dir / "calendar.json"

    @property
    def workspace_file(self) -> Path:
        return self.dir / "workspace.json"

    def domain(self, name: str) -> Path:
        key = (name or "").strip().lower()
        if key in DIR_DOMAINS:
            return self._domain(key) if key in DOMAIN_ENV else self.dir / key
        table = {
            "claude": self.claude,
            "identity": self.identity,
            "members": self.members,
            "config": self.config,
            "secrets": self.secrets,
            "store": self.store,
            "workspace": self.workspace_file,
            "sync_config": self.sync_config,
            "mcp": self.mcp_json,
            "calendar": self.calendar,
        }
        if key in table:
            return table[key]
        raise KeyError(f"unknown workspace domain: {name}")

    # --- lifecycle -----------------------------------------------------

    def ensure(self) -> "Workspace":
        self.dir.mkdir(parents=True, exist_ok=True)
        for name in DIR_DOMAINS:
            self.domain(name).mkdir(parents=True, exist_ok=True)
        return self

    def init(self, *, label: str = "", default_persona: str = "") -> "Workspace":
        self.ensure()
        meta = {}
        if self.workspace_file.is_file():
            try:
                prev = json.loads(self.workspace_file.read_text(encoding="utf-8"))
                if isinstance(prev, dict):
                    meta.update(prev)
            except (OSError, json.JSONDecodeError):
                pass
        meta.update(
            {
                "id": self.id or self.dir.name or f"ws_{secrets.token_hex(8)}",
                "label": label or self.label or self.dir.name,
                "created_at": meta.get("created_at")
                or datetime.now(timezone.utc).isoformat(),
                "default_persona": default_persona or self.default_persona or "default",
            }
        )
        if self.parent_workspace:
            meta["parent_workspace"] = self.parent_workspace
        if self.isolation:
            meta["isolation"] = self.isolation
        if self.image:
            meta["image"] = self.image
        self.workspace_file.write_text(
            json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return replace(
            self,
            id=meta["id"],
            label=meta["label"],
            default_persona=meta["default_persona"],
            parent_workspace=str(meta.get("parent_workspace") or ""),
        )

    def migrate(self, *, dry_run: bool = False) -> list[tuple[Path, Path]]:
        """Move legacy host-level data into this workspace via the C80 planner."""
        from .migrate import _norm, execute_migration, plan_migration

        ws_root = _norm(self.dir)
        moves = [
            m
            for m in plan_migration(self.host, ws_id=self.id)
            if m.kind == "ws"
            or (m.kind == "external" and _norm(m.dst).startswith(ws_root))
        ]
        execute_migration(moves, self.host, dry_run=dry_run)
        return [(m.src, m.dst) for m in moves]

    @staticmethod
    def env_keys() -> tuple[str, ...]:
        """Every env var the resolver owns (set or removed by ``env()``)."""
        return tuple(sorted(_ENV_KEYS))

    def env(
        self,
        base: Mapping[str, str] | None = None,
        *,
        persona: str = "",
        workdir: str | Path | None = None,
    ) -> dict[str, str]:
        """Process env for a turn or job inside this workspace.

        Sets every resolver-owned key to this workspace's paths (new names
        plus one-release aliases) and strips removed keys, so no value from
        a parent process or a foreign workspace can leak into the child.
        """
        out = dict(os.environ if base is None else base)
        for key in _ENV_KEYS:
            out.pop(key, None)
        workdir_p = _path(str(workdir)) if workdir else self.workdir
        out[ENV_HOST_DIR] = str(self.host.dir)
        out[ENV_WORKSPACE] = self.id
        out[ENV_WORKDIR] = str(workdir_p)
        if persona:
            out["AGENTS_PERSONA"] = persona
        host = self.host
        out["AGENTS_KEYS_DIR"] = str(host.keys)
        pairs = (
            ("AGENTS_MEMORY_DIR", "AGENTS_MEMORY_PATH", self.memory),
            ("AGENTS_RELAY_DIR", "AGENTS_RELAY_STATE", self.relay_state),
            ("AGENTS_RULES_DIR", None, self.rules),
            ("AGENTS_SKILLS_DIR", None, self.skills),
            ("AGENTS_MODULES_DIR", None, self.modules),
            ("AGENTS_SCHEDULES_DIR", None, self.schedules),
            ("AGENTS_TRACES_DIR", None, self.traces),
            ("AGENTS_ROUTINE_RUNS_DIR", None, self.routine_runs),
            ("AGENTS_PERSONAS_DIR", None, self.personas),
            ("AGENTS_CONTENT_DIR", None, self.content),
            ("AGENTS_BROWSER_PROFILE", "AGENTS_BROWSER_SESSION", host.browser),
            (ENV_USERS_FILE, ENV_USERS_FILE_LEGACY, self.identity),
            ("AGENTS_KNOWN_DIDS_FILE", "AGENTS_KNOWN_DIDS",
             host.keys / "known-dids.jsonl"),
            ("AGENTS_MCP_CONFIG_FILE", "AGENTS_MCP_CONFIG", host.mcp_json),
            ("AGENTS_MCP_CACHE_FILE", "AGENTS_MCP_CACHE",
             host.cache / "mcp-tools-cache.json"),
            ("AGENTS_SYNC_CONFIG_FILE", "AGENTS_SYNC_CONFIG", self.sync_config),
        )
        for new_name, alias, value in pairs:
            out[new_name] = str(value)
            if alias:
                out[alias] = str(value)
        return out

    # --- reporting -----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "host": str(self.host.dir),
            "dir": str(self.dir),
            "root": str(self.dir),
            "workdir": str(self.workdir),
            "id": self.id,
            "label": self.label,
            "default_persona": self.default_persona,
            "parent_workspace": self.parent_workspace,
            "domains": {name: str(self.domain(name)) for name in DIR_DOMAINS},
            "claude": str(self.claude),
            "identity": str(self.identity),
            "members": str(self.members),
            "config": str(self.config),
            "secrets": str(self.secrets),
            "store": str(self.store),
        }


def current() -> Workspace:
    """The workspace selected for this process (CLI entry only)."""
    return Workspace.from_env()
