"""Host > Workspace > Persona > Session resolver.

The Host (``AGENTS_HOST_DIR``, default ``~/.agents``) holds infra only:
keys, secrets, run, cache, logs, clients, agents.json, users.json.
Every Workspace lives at ``<host>/workspaces/<id>`` and holds the domains
(memory, skills, modules, schedules, traces, routine-runs, relay, personas,
rules, content, work). A workspace is selected per call by id
(``AGENTS_WORKSPACE=<id>``), never by a process-global root.

Deprecated aliases (one release, then removed): ``AGENTS_WORKSPACE_DIR`` -> ``AGENTS_WORKDIR``,
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
import subprocess
import shutil
import sys
import uuid
import warnings
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from .compat import read_alias

ENV_HOST_DIR = "AGENTS_HOST_DIR"
ENV_WORKSPACE = "AGENTS_WORKSPACE"  # workspace id, not a path
ENV_WORKDIR = "AGENTS_WORKDIR"
ENV_WORKDIR_LEGACY = "AGENTS_WORKSPACE_DIR"  # deprecated alias, one release
ENV_USERS_FILE = "AGENTS_USERS_FILE"  # -> <host>/users.json
ENV_USERS_FILE_LEGACY = "AGENTS_IDENTITY_PATH"  # deprecated alias, one release
ENV_WORKSPACES_DIR = "AGENTS_WORKSPACES_DIR"  # deprecated: <host>/workspaces
ENV_ROOT = "AGENTS_WORKSPACE_ROOT"  # deprecated: ad-hoc workspace dir
ENV_WORKSPACE_ID_LEGACY = "AGENTS_WORKSPACE_ID"
ENV_DELEGATE_DEPTH = "AGENTS_DELEGATE_DEPTH"
ENV_DELEGATE_MAX_DEPTH = "AGENTS_DELEGATE_MAX_DEPTH"

# Env vars removed by the Host/Workspace split. Workspace.env() strips them
# from child envs so a parent can never leak them into a turn.
REMOVED_ENV = ("AGENTS_DIR",)

# Host infrastructure secrets must never reach agent turns, tool jails,
# containers, or workspace overrides.
INFRA_SECRET_KEYS = frozenset({
    "RELAY_SECRET",
    "GATEWAY_SECRET",
    "RELAY_WEB_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "WEBHOOK_SECRET",
    "DOCKER_HOST",
    "AGENTS_SYNC_SERVER_SECRET",
    "AGENTS_TERMINAL_NOTIFY_SECRET",
})

LLM_SECRET_KEYS = frozenset({
    "LLM_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "LMSTUDIO_API_KEY",
    "AZURE_SPEECH_KEY",
    "AZURE_TTS_KEY",
})

_TOOL_ONLY_STRIP_KEYS = frozenset({
    "AGENTS_APPROVAL_TOKEN",
    "AGENTS_ACP_API_KEY",
    "AGENTS_SYNC_TOKEN",
    "AGENTS_CALENDAR_PASSWORD",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GIT_ASKPASS",
})

_ACP_STRIP_PREFIXES = (
    "LLM_",
    "OPENAI_",
    "OPENROUTER_",
    "ANTHROPIC_",
    "LMSTUDIO_",
    "DEEPSEEK_",
    "GEMINI_",
    "GROQ_",
    "MISTRAL_",
    "AZURE_",
    "RELAY_",
    "TELEGRAM_",
    "GIT_ASKPASS",
)

_ENV_ROLES = frozenset({"default", "turn", "tool", "acp", "container"})

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
        ENV_WORKSPACE,
        ENV_WORKDIR,
        ENV_WORKDIR_LEGACY,
        ENV_USERS_FILE,
        ENV_USERS_FILE_LEGACY,
        ENV_WORKSPACES_DIR,
        ENV_ROOT,
        ENV_WORKSPACE_ID_LEGACY,
        "AGENTS_HOME",  # dead alias: strip so children never inherit it
        "AGENTS_PERSONA",
        "AGENTS_KEYS_DIR",
        "AGENTS_MEMORY_DIR",
        "AGENTS_RELAY_DIR",
        "AGENTS_CONTENT_DIR",
        "AGENTS_BROWSER_HOME",
        "AGENTS_BROWSER_PROFILE",
        "AGENTS_BROWSER_PROFILE_NAME",
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
    """Resolve the Host dir: AGENTS_HOST_DIR > ~/.agents."""
    env = os.environ if env is None else env
    raw = _clean(env, ENV_HOST_DIR)
    if raw:
        return _path(raw)
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


def host_config(
    host: "Host | None" = None, *, env: Mapping[str, str] | None = None
) -> dict:
    """Read ``<host>/agents.json`` as a dict (empty dict when absent/invalid)."""
    target = host or Host.from_env(env)
    return _json_dict(target.agents_json)


def workspace_config(ws: "Workspace | None" = None) -> dict:
    """Read ``<ws>/workspace.json`` as a dict (empty dict when absent/invalid)."""
    target = ws or Workspace.from_env()
    return _json_dict(target.dir / "workspace.json")


def _read_meta(root: Path) -> dict:
    return _json_dict(root / "workspace.json")


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
    def secrets_dir(self) -> Path:
        return self.dir / "secrets"

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
    def serve_state_path(self) -> Path:
        """Live-serve state file ``<host>/run/serve.json``."""
        return self.run / "serve.json"

    @property
    def mcp_json(self) -> Path:
        return self.dir / "mcp.json"

    @property
    def mcp_dir(self) -> Path:
        return self.dir / "mcp"

    @property
    def providers(self) -> Path:
        return self.dir / "providers"

    @property
    def approval_allowlist(self) -> Path:
        return self.dir / "approval-allowlist.json"

    @property
    def host_paths(self) -> Path:
        return self.dir / "host_paths.json"

    def host_id(self) -> str:
        """Canonical host identity.

        Order: ``agents.json["host_id"]`` > optional ``agents_keys`` DID
        lookup (never reads private key bytes) > freshly minted
        ``host_<uuid4hex>`` persisted atomically into ``agents.json``.
        """
        existing = host_config(self).get("host_id")
        if isinstance(existing, str) and existing.strip():
            return existing.strip()
        did = _agents_keys_did()
        if did:
            return did
        host_id = f"host_{uuid.uuid4().hex}"
        data = host_config(self)
        data["host_id"] = host_id
        _atomic_json_write(self.agents_json, data)
        return host_id

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
        isolation: str = "",
        image: str = "",
        env: Mapping[str, str] | None = None,
    ) -> "Workspace":
        """Create ``<host>/workspaces/<id>`` plus its first persona."""
        base = self.workspace(ws_id, env=env)
        if isolation or image:
            base = replace(base, isolation=isolation, image=image)
        ws = base.init(
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
        self,
        ws_id: str | None = None,
        *,
        env: Mapping[str, str] | None = None,
        strict: bool = False,
    ) -> "Workspace":
        """explicit id > AGENTS_WORKSPACE > the only existing workspace > default_workspace.

        First start (no workspaces yet) creates ``default``. When several
        workspaces exist without an id, non-strict callers resolve
        ``agents.json -> default_workspace`` or an existing ``default``
        workspace; ``strict=True`` raises ``ValueError``.
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
        if not strict:
            configured = str(host_config(self).get("default_workspace") or "").strip()
            if configured:
                if not self.workspace_exists(configured, env=env):
                    raise ValueError(
                        f"configured default_workspace does not exist: {configured!r}"
                    )
                return self.workspace(configured, env=env)
            if self.workspace_exists("default", env=env):
                return self.workspace("default", env=env)
        raise ValueError(
            "several workspaces exist; pass a workspace id "
            f"({ENV_WORKSPACE} or --workspace)"
        )

    def resolve_entry_workspace(
        self, env: Mapping[str, str] | None = None
    ) -> "Workspace":
        """Deterministic entry workspace for serve/CLI boot.

        Precedence: explicit ``AGENTS_WORKSPACE`` (strict; unknown id
        raises) > ``agents.json["default_workspace"]`` when it exists on
        disk > an existing ``default`` workspace > lexicographically
        first existing workspace > lazily created ``default``. Never
        crashes on multi-workspace hosts without a configured default.
        """
        env = os.environ if env is None else env
        if _clean(env, ENV_WORKSPACE):
            return self.select_workspace(env=env, strict=True)
        configured = str(host_config(self).get("default_workspace") or "").strip()
        if configured and self.workspace_exists(configured, env=env):
            return self.workspace(configured, env=env)
        if self.workspace_exists("default", env=env):
            return self.workspace("default", env=env)
        found = self.list_workspaces(env=env)
        if found:
            return sorted(found, key=lambda ws: ws.dir.name)[0]
        return self.create("default", label="Default", env=env)

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
        try:
            from .runtime import docker_bin, LABEL

            if docker_bin() is not None:
                subprocess.run(
                    [docker_bin(), "rm", "-f", f"{LABEL}-{ws_id}"],
                    capture_output=True, timeout=15,
                )
        except Exception:
            pass  # container cleanup is best-effort; the sweep handles strays
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
    def _is_legacy_root(host_dir: Path) -> bool:
        """A host that was never migrated: no workspaces/, legacy domains on top."""
        if (host_dir / "workspaces").is_dir():
            return False
        markers = (
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
    def browser_profile(self) -> str:
        """Host browser session profile name for this workspace."""
        val = str(_read_meta(self.dir).get("browser_profile") or "").strip()
        return val or "default"

    @property
    def run(self) -> Path:
        """Ephemeral runtime dir under the workspace (never synced)."""
        return self.dir / "run"

    @property
    def delegates(self) -> Path:
        return self.run / "delegates"

    @property
    def procs(self) -> Path:
        return self.run / "procs"

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
    def secrets_dir(self) -> Path:
        return self.dir / "secrets"

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
            "run": self.run,
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
        """Write ``workspace.json`` lazily without pre-creating empty domain dirs."""
        self.dir.mkdir(parents=True, exist_ok=True)
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
        role: str = "default",
        provider_key_env: str = "",
        extra_allow: Iterable[str] = (),
    ) -> dict[str, str]:
        """Process env for a turn or job inside this workspace.

        Sets every resolver-owned key to this workspace's paths (new names
        plus one-release aliases) and strips removed keys, so no value from
        a parent process or a foreign workspace can leak into the child.
        ``role`` narrows sensitive secrets by execution role: ``default``,
        ``turn``, ``tool``, ``acp``, or ``container``. When ``provider_key_env``
        is empty on ``turn``/``container``, LLM provider keys stay intact to
        support interactive per-chat model switching.
        """
        if role not in _ENV_ROLES:
            raise ValueError(f"unknown workspace env role: {role!r}")
        raw_base = dict(os.environ if base is None else base)
        out = dict(raw_base)
        for key in _ENV_KEYS:
            out.pop(key, None)
        allow = {str(k).strip() for k in extra_allow if str(k).strip()}
        if role != "default":
            for key in INFRA_SECRET_KEYS:
                if key not in allow:
                    out.pop(key, None)
            if "AGENTS_SYNC_TOKEN" not in allow:
                out.pop("AGENTS_SYNC_TOKEN", None)
        if role in ("turn", "container") and provider_key_env:
            for key in LLM_SECRET_KEYS:
                if key != provider_key_env and key not in allow:
                    out.pop(key, None)
        elif role == "tool":
            for key in (*LLM_SECRET_KEYS, *_TOOL_ONLY_STRIP_KEYS):
                if key not in allow:
                    out.pop(key, None)
        elif role == "acp":
            for key in list(out):
                if key in allow:
                    continue
                if (
                    key in LLM_SECRET_KEYS
                    or key in _TOOL_ONLY_STRIP_KEYS
                    or any(key.startswith(p) for p in _ACP_STRIP_PREFIXES)
                ):
                    out.pop(key, None)
        for key in allow:
            if key in raw_base and key not in _ENV_KEYS:
                out[key] = raw_base[key]
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
            ("AGENTS_BROWSER_HOME", None, host.browser),
            ("AGENTS_BROWSER_PROFILE_NAME", None, self.browser_profile),
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
        # AGENTS_BROWSER_PROFILE/SESSION are pass-through only: an explicit
        # caller choice (profile name or dir) survives, but env() no longer
        # overloads PROFILE with the browser state root — AGENTS_BROWSER_HOME
        # carries that.
        for legacy in ("AGENTS_BROWSER_PROFILE", "AGENTS_BROWSER_SESSION"):
            if raw_base.get(legacy):
                out[legacy] = raw_base[legacy]
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


def denied_paths(ws: Workspace, env: Mapping[str, str] | None = None) -> list[Path]:
    """Paths always denied to terminal/agent file & shell tools.

    Evaluated before ``allowed_roots(ws)`` so host secrets, private keys,
    logs, client prefs/registries, pending approvals, and control-plane
    files (or sensitive files inside ``ws.dir``) stay unreachable even when
    ``ws.dir`` or ``host.run`` is allowed.
    """
    host = ws.host
    env_map = ws.source_env if env is None and ws.source_env else env
    return [
        host.keys,
        host.secrets,
        host.secrets_dir,
        host.mcp_json,
        host.mcp_dir,
        host.agents_json,
        host.users_file(env_map),
        host.approval_allowlist,
        host.run / "approvals",
        host.logs,
        host.clients,
        ws.secrets,
        ws.members,
    ]


def allowed_roots(ws: Workspace, env: Mapping[str, str] | None = None) -> list[Path]:
    """Dirs the workspace lock must not treat as escapes.

    Single source for the root list the harness executor, the terminal
    policy, and the delegate clamp honor: the workspace tree plus
    non-secret host runtime dirs (``run``, ``cache``; ``run/approvals``,
    ``logs``, and ``clients`` stay denied via ``denied_paths(ws)``) plus
    env-listed dirs. Keys, secrets, control-plane files, and MCP configs
    are excluded (see ``denied_paths(ws)``). Host memory/skills do not
    exist — they are never allowed (D-host-layer). Callers that also
    jail on the workdir add ``ws.workdir`` themselves.
    """
    env = os.environ if env is None else env
    host = ws.host
    roots = [
        ws.dir,
        host.run,
        host.cache,
    ]
    for key in (
        "AGENTS_SKILLS_DIR",
        "AGENTS_MODULES_DIR",
        "AGENTS_MEMORY_PATH",
        "AGENTS_TRACES_DIR",
        "AGENTS_SCHEDULES_DIR",
        "AGENTS_ROUTINE_RUNS_DIR",
    ):
        raw = _clean(env, key)
        if raw:
            roots.append(Path(raw).expanduser())
    roots.extend(
        Path(part.strip()).expanduser()
        for part in _clean(env, "AGENTS_SKILLS_EXTRA").split(os.pathsep)
        if part.strip()
    )
    allow = read_alias(env, "AGENTS_WORKDIR_ALLOW", "AGENTS_WORKSPACE_ALLOW")
    roots.extend(
        Path(part.strip()).expanduser() for part in allow.split(os.pathsep) if part.strip()
    )
    return roots


def _int_env(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(_clean(env, name)) or default
    except ValueError:
        return default


def delegate_depth(env: Mapping[str, str] | None = None) -> int:
    """Current ``AGENTS_DELEGATE_DEPTH`` (0 when unset or unparseable)."""
    env = os.environ if env is None else env
    return max(0, _int_env(env, ENV_DELEGATE_DEPTH, 0))


def delegate_env(
    env: Mapping[str, str] | None = None, *, max_depth: int = 0
) -> dict[str, str]:
    """Child env for a delegate turn: ``AGENTS_DELEGATE_DEPTH`` + 1.

    Advisory anti-accident guard (env-hint, PERIMETER.md): the counter
    travels in the child env so nested delegate turns accumulate depth.
    ``max_depth`` defaults to ``AGENTS_DELEGATE_MAX_DEPTH`` or 8; hitting
    the limit raises in the parent, which the child cannot reach. The
    hard boundary stays the deploy container — in-turn isolation is
    advisory, never a jail.
    """
    out = dict(os.environ if env is None else env)
    limit = max_depth or _int_env(out, ENV_DELEGATE_MAX_DEPTH, 8)
    depth = delegate_depth(out)
    if depth >= limit:
        raise ValueError(f"delegate depth limit reached ({depth}/{limit})")
    out[ENV_DELEGATE_DEPTH] = str(depth + 1)
    return out


def _json_dict(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_json_write(path: Path, data: Mapping) -> Path:
    """Atomically write ``data`` as pretty JSON (0600 on POSIX)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    payload = json.dumps(dict(data), indent=2, sort_keys=True) + "\n"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)
        if os.name != "nt":
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return path


def _agents_keys_did() -> str:
    """Best-effort DID lookup via the optional ``agents_keys`` CLI.

    Delegates any key material handling to ``agents_keys``; this process
    never reads raw ``.ed25519`` bytes. Returns "" when the package or a
    host key is unavailable.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "agents_keys", "did", "host"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    for line in (proc.stdout or "").splitlines():
        cand = line.strip()
        if cand.startswith("did:key:"):
            return cand
    return ""


def read_serve_state(host: Host | None = None) -> dict | None:
    """Read ``<host>/run/serve.json``; ``None`` when missing or malformed.

    Read-only compat fallback: a legacy ``<host>/run/serve.pid`` file
    (plain PID) is surfaced as ``{"pid": ...}`` with a
    ``DeprecationWarning``.
    """
    target = host or Host.from_env()
    path = target.serve_state_path
    if path.is_file():
        data = _json_dict(path)
        return data or None
    legacy = target.run / "serve.pid"
    if legacy.is_file():
        warnings.warn(
            f"legacy serve pid file {legacy} read; move to run/serve.json",
            DeprecationWarning,
            stacklevel=2,
        )
        try:
            pid = int(legacy.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        return {"pid": pid} if pid > 0 else None
    return None


def write_serve_state(
    host: Host | None = None,
    *,
    pid: int,
    port: int,
    url: str,
    host_id: str | None = None,
) -> Path:
    """Atomically write ``<host>/run/serve.json`` and return its path."""
    target = host or Host.from_env()
    target.run.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": int(pid),
        "port": int(port),
        "url": str(url),
        "host_id": host_id or target.host_id(),
    }
    return _atomic_json_write(target.serve_state_path, payload)


def clear_serve_state(
    host: Host | None = None, *, expected_pid: int | None = None
) -> bool:
    """Remove ``serve.json``; with ``expected_pid`` only on pid match."""
    target = host or Host.from_env()
    path = target.serve_state_path
    if not path.is_file():
        return False
    if expected_pid is not None:
        data = _json_dict(path)
        if data.get("pid") != expected_pid:
            return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def resolve_secret(
    key: str,
    ws: Workspace | None = None,
    env: Mapping[str, str] | None = None,
    *,
    scope: str = "auto",
    host: Host | None = None,
) -> str:
    """Resolve ``key`` via Host Secret Vault (see ``agents_workspace.secrets``)."""
    from .secrets import resolve_secret as _resolve

    return _resolve(key, ws=ws, env=env, scope=scope, host=host)


def current() -> Workspace:
    """The workspace selected for this process (CLI entry only)."""
    return Workspace.from_env()
