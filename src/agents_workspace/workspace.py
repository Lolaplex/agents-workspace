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

import hashlib
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
from typing import Any, Iterable, Mapping

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
        "AGENTS_STORE_DB",
        "AGENTS_WORKDIR_LOCK",
        "AGENTS_WORKDIR_ALLOW",
        "AGENTS_WORKSPACE_ALLOW",
        *REMOVED_ENV,
        *DOMAIN_ENV.values(),
        *DOMAIN_ENV_ALIASES.values(),
    )
)

_ID_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
# HLC-safe sync node id (agents-store _NODE_RE forbids ":" in node ids).
_NODE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# Sentinel for "argument not passed" in update helpers (None means clear).
_UNSET: Any = object()

ALLOWED_APPROVAL_MODES = ("off", "ask", "strict")

# Keys that must never land in a non-secret <ws>/config.json.
_SECRETISH_KEY = re.compile(
    r"(SECRET|TOKEN|PASSWORD|API[_-]?KEY|PRIVATE)", re.IGNORECASE
)


def _blocked_host_dir(host: "Host", resolved: Path) -> bool:
    """True when ``resolved`` sits inside a host infra dir (secrets/keys)."""
    for root in (host.secrets_dir, host.keys):
        try:
            if resolved == root or resolved.is_relative_to(root):
                return True
        except OSError:
            return True
    return False


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


# sync_hosts entries are peer ids *as the sync hub sees them*: the channel
# device id the hub minted at pairing (agents-relay ``/v1/hub/devices``).
# They are NOT Host.host_id() values -- ``default_host`` is the host-picker
# id; ``sync_hosts`` is the hub fan-out allowlist. Empty = CLOSED (zero
# remote devices permitted); only an explicit device id is allowed.
_PEER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")

_SYNC_POLICIES = ("local", "synced", "shared")
# default_host: host-picker id (klanker#133); strict, never coerced.
_HOST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# repos[] slugs look like "org/app"; ".." is forbidden (no path escapes).
_REPO_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_EXEC_KEYS = frozenset({"owner", "failover", "lease_sec"})
# Read fallback when workspace.json execution is present but unparsable.
_EXEC_FALLBACK = {"failover": "none", "lease_sec": 45}


def _validate_sync_policy(value) -> str:
    """Strict write-side policy: normalized to local|synced|shared."""
    policy = str(value or "").strip().lower()
    if policy not in _SYNC_POLICIES:
        raise ValueError(
            f"sync_policy must be local|synced|shared: {value!r}")
    return policy


def _validate_sync_hosts(value) -> list[str]:
    """Strict write-side validation: list/tuple of peer-id strings."""
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or isinstance(value, Mapping) \
            or not isinstance(value, (list, tuple)):
        raise ValueError(
            "sync_hosts must be a list of hub device ids, got "
            f"{type(value).__name__}")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(
                f"sync_hosts entries must be strings: {item!r}")
        peer = item.strip()
        if not _PEER_ID_RE.match(peer):
            raise ValueError(f"invalid sync_hosts device id: {item!r}")
        if peer not in out:
            out.append(peer)
    return out


def _validate_default_host(value) -> str:
    """Strict write-side default_host: host-id string or "" (no coercion)."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(
            f"default_host must be a string id, got {type(value).__name__}")
    clean = value.strip()
    if not clean:
        return ""
    if not _HOST_ID_RE.match(clean):
        raise ValueError(f"invalid default_host id: {value!r}")
    return clean


def _validate_repo_slug(slug) -> str:
    clean = str(slug or "").strip()
    if not clean or ".." in clean or not _REPO_SLUG_RE.match(clean):
        raise ValueError(f"invalid repo slug: {slug!r}")
    return clean


def _validate_repos(repos) -> tuple:
    """Strict write-side repos[]: {slug, url, checkout_mode} dicts.

    ``path`` keys are refused outright: checkout paths stay host-local in
    ``<host>/host_paths.json`` and must never travel in workspace.json.
    """
    if repos is None:
        return ()
    if isinstance(repos, (str, bytes)) or isinstance(repos, Mapping) \
            or not isinstance(repos, (list, tuple)):
        raise ValueError(
            f"repos must be a list of repo mappings, got {type(repos).__name__}")
    out: list[dict] = []
    seen: set[str] = set()
    for r in repos:
        if not isinstance(r, Mapping):
            raise ValueError(f"repos entries must be mappings: {r!r}")
        if "path" in r:
            raise ValueError(
                "repos entries must not carry a host-local 'path' key; "
                "use host_paths.json")
        slug = _validate_repo_slug(r.get("slug"))
        if slug in seen:
            raise ValueError(f"duplicate repo slug: {slug!r}")
        seen.add(slug)
        mode = str(r.get("checkout_mode") or "shared")
        if mode not in ("shared", "worktree"):
            raise ValueError(
                f"checkout_mode must be shared|worktree: {mode!r}")
        out.append(
            {"slug": slug,
             "url": str(r.get("url") or ""),
             "checkout_mode": mode})
    return tuple(out)


def _validate_execution(value) -> dict:
    """Strict write-side execution: {owner, failover, lease_sec} only.

    Secret-looking keys are rejected; ``lease_sec`` must be a real int
    (never bool) inside [15, 3600]; ``owner`` a peer id or "".
    """
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(
            f"execution must be a mapping, got {type(value).__name__}")
    _reject_secret_config_keys(value)
    unknown = set(str(k) for k in value) - _EXEC_KEYS
    if unknown:
        raise ValueError(
            f"unknown execution keys: {sorted(unknown)} "
            f"(allowed: {sorted(_EXEC_KEYS)})")
    out: dict = {}
    if "owner" in value:
        owner = value["owner"]
        if owner is None or (isinstance(owner, str) and not owner.strip()):
            pass  # empty clears
        elif isinstance(owner, str) and _PEER_ID_RE.match(owner.strip()):
            out["owner"] = owner.strip()
        else:
            raise ValueError(f"invalid execution.owner peer id: {owner!r}")
    if "failover" in value:
        failover = str(value["failover"] or "none")
        if failover not in ("standby", "none"):
            raise ValueError(
                f"execution.failover must be standby|none: {failover!r}")
        out["failover"] = failover
    if "lease_sec" in value:
        lease = value["lease_sec"]
        if isinstance(lease, bool) or not isinstance(lease, int) \
                or not (15 <= lease <= 3600):
            raise ValueError(
                "execution.lease_sec must be an int in [15, 3600]: "
                f"{lease!r}")
        out["lease_sec"] = lease
    return out


def _read_sync_policy(value) -> tuple[str, bool]:
    """Tolerant read: missing/empty -> ``local``; unknown -> ``local`` +
    error flag (fail closed, never widen access)."""
    if value is None:
        return "local", False
    if not isinstance(value, str):
        return "local", True
    policy = value.strip().lower()
    if not policy:
        return "local", False
    if policy in _SYNC_POLICIES:
        return policy, False
    return "local", True


def _read_sync_hosts(value) -> tuple[tuple, bool]:
    """Tolerant read-side parse: a hand-edited bad value never crashes
    workspace loading AND never widens access -- any malformed shape
    (non-list, or any invalid peer id) fails closed to ``()`` + error."""
    if value is None:
        return (), False
    if isinstance(value, (str, bytes)) or isinstance(value, Mapping) \
            or not isinstance(value, (list, tuple)):
        return (), True
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _PEER_ID_RE.match(item.strip()):
            return (), True
        if item.strip() not in out:
            out.append(item.strip())
    return tuple(out), False


def _read_default_host(value) -> tuple[str, bool]:
    """Tolerant read: only a valid host-id string survives (no coercion)."""
    if value is None or value == "":
        return "", False
    if isinstance(value, str) and _HOST_ID_RE.match(value.strip()):
        return value.strip(), False
    return "", True


def _read_repos(value) -> tuple[tuple, bool]:
    """Tolerant read: malformed repos never crash ``host.workspace()``;
    any invalid entry fails closed to ``()`` + error."""
    if value is None:
        return (), False
    try:
        return _validate_repos(value), False
    except ValueError:
        return (), True


def _read_execution(value) -> tuple[dict, bool]:
    """Tolerant read: malformed values fall back to safe defaults
    (``failover=none``, ``lease_sec=45``) instead of crashing."""
    if value is None:
        return {}, False
    if not isinstance(value, Mapping):
        return dict(_EXEC_FALLBACK), True
    out: dict = {}
    err = False
    for key in value:
        if key not in _EXEC_KEYS:
            err = True
    if "owner" in value:
        owner = value["owner"]
        if owner is None or (isinstance(owner, str) and not owner.strip()):
            out["owner"] = ""
        elif isinstance(owner, str) and _PEER_ID_RE.match(owner.strip()):
            out["owner"] = owner.strip()
        else:
            out["owner"] = ""
            err = True
    if "failover" in value:
        failover = value["failover"]
        if failover in ("standby", "none"):
            out["failover"] = failover
        else:
            out["failover"] = "none"
            err = True
    if "lease_sec" in value:
        lease = value["lease_sec"]
        if isinstance(lease, int) and not isinstance(lease, bool) \
                and 15 <= lease <= 3600:
            out["lease_sec"] = lease
        else:
            out["lease_sec"] = 45
            err = True
    return out, err


def _ownership_from_meta(meta: Mapping) -> dict:
    """Parse the 5 ownership fields off ``workspace.json`` (read side).

    Never raises: malformed fields fail closed and set ``_policy_error`` so
    a hand-edited workspace.json can never silently widen sync access.
    """
    policy, perr = _read_sync_policy(meta.get("sync_policy"))
    hosts, herr = _read_sync_hosts(meta.get("sync_hosts"))
    dhost, derr = _read_default_host(meta.get("default_host"))
    repos, rerr = _read_repos(meta.get("repos"))
    execution, eerr = _read_execution(meta.get("execution"))
    # Effective 'local' + non-empty allowlist is a hand-edited contradiction:
    # it fails closed at runtime but must surface as a policy error.
    policy_error = perr or herr or derr or rerr or eerr or (
        policy == "local" and bool(hosts)
    )
    if policy_error:
        warnings.warn(
            "malformed ownership field(s) in workspace.json "
            f"({meta.get('id') or '?'}); failing closed",
            UserWarning,
            stacklevel=3,
        )
    return {
        "sync_policy": policy,
        "sync_hosts": hosts,
        "default_host": dhost,
        "repos": repos,
        "execution": execution,
        "_policy_error": policy_error,
    }


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
        did = _agents_keys_did(self)
        if did:
            return did
        host_id = f"host_{uuid.uuid4().hex}"
        data = host_config(self)
        data["host_id"] = host_id
        _atomic_json_write(self.agents_json, data)
        return host_id

    def sync_node_id(self) -> str:
        """HLC-safe node identifier derived from ``host_id()``.

        Ids matching ``_NODE_ID_RE`` (``^[A-Za-z0-9_-]{1,32}$``) pass
        through unchanged. Anything else — including ``host_<uuid4hex>``
        (37 chars, over the 32-char cap) and DID-style ids whose ``:``
        breaks the agents-store ``_NODE_RE`` — maps to a stable
        ``k_<sha256[:16]>`` token instead.
        """
        hid = self.host_id()
        if _NODE_ID_RE.match(hid):
            return hid
        digest = hashlib.sha256(hid.encode("utf-8")).hexdigest()[:16]
        return f"k_{digest}"

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

    # --- host-local repo checkouts (host_paths.json) --------------------

    def read_host_paths(self) -> dict[str, dict[str, str]]:
        """Read ``<host>/host_paths.json``: ``{ws_id: {slug: path}}``.

        The ``""`` bucket holds checkouts shared across workspaces. Returns
        only string->string entries; corrupt shapes are dropped.
        """
        data = _json_dict(self.host_paths)
        out: dict[str, dict[str, str]] = {}
        for ws_id, mapping in data.items():
            if not isinstance(ws_id, str) or not isinstance(mapping, Mapping):
                continue
            inner = {
                str(slug): str(path)
                for slug, path in mapping.items()
                if isinstance(slug, str) and isinstance(path, str)
                and slug and path
            }
            if inner:
                out[ws_id] = inner
        return out

    def set_host_repo_path(self, slug: str, path: str, *, ws_id: str = "") -> Path:
        """Record a host-local checkout path for repo ``slug`` (optionally
        per workspace) and atomically rewrite ``host_paths.json``."""
        clean_slug = _validate_repo_slug(slug)
        clean_ws = (ws_id or "").strip()
        raw = str(path or "").strip()
        if not raw or "\x00" in raw:
            raise ValueError(f"invalid host repo path: {path!r}")
        data = _json_dict(self.host_paths)
        bucket = data.get(clean_ws)
        bucket = dict(bucket) if isinstance(bucket, Mapping) else {}
        bucket[clean_slug] = raw
        data[clean_ws] = bucket
        return _atomic_json_write(self.host_paths, data)

    def resolve_repo_path(
        self, slug: str, *, ws_id: str = ""
    ) -> Path | None:
        """Checkout path for ``slug``: ``ws_id`` bucket wins, ``""`` falls
        back as the shared default; ``None`` when unmapped."""
        paths = self.read_host_paths()
        keys = [ws_id.strip(), ""] if (ws_id or "").strip() else [""]
        for key in keys:
            val = paths.get(key, {}).get(slug)
            if val:
                return Path(val).expanduser()
        return None

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
            **_ownership_from_meta(meta),
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
        sync_policy: str | None = None,
        sync_hosts: list | tuple | None = None,
        default_host: str | None = None,
        repos: list | tuple | None = None,
        execution: Mapping | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "Workspace":
        """Create ``<host>/workspaces/<id>`` plus its first persona.

        Ownership fields default fail-closed (``sync_policy="local"``,
        ``sync_hosts=()``); ``None`` keeps the on-disk value when the
        workspace already exists. ``local`` + non-empty ``sync_hosts``
        raises ``ValueError``.
        """
        base = self.workspace(ws_id, env=env)
        policy = (
            base.sync_policy
            if sync_policy is None
            else _validate_sync_policy(sync_policy)
        )
        hosts = (
            base.sync_hosts
            if sync_hosts is None
            else tuple(_validate_sync_hosts(sync_hosts))
        )
        dhost = (
            base.default_host
            if default_host is None
            else _validate_default_host(default_host)
        )
        repos_t = base.repos if repos is None else _validate_repos(repos)
        exec_d = (
            dict(base.execution)
            if execution is None
            else _validate_execution(execution)
        )
        if policy == "local" and hosts:
            raise ValueError(
                "sync_policy 'local' forbids non-empty sync_hosts; "
                "set sync_policy='synced' or 'shared' explicitly"
            )
        base = replace(
            base,
            sync_policy=policy,
            sync_hosts=hosts,
            default_host=dhost,
            repos=repos_t,
            execution=exec_d,
        )
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

    def update_workspace(
        self,
        ws_id: str,
        *,
        label: str | None = None,
        workdir: str | None = _UNSET,
        workdir_lock: bool | None = None,
        browser_profile: str | None = _UNSET,
        sync_policy: str | None = _UNSET,
        sync_hosts: list | tuple | None = _UNSET,
        default_host: str | None = _UNSET,
        repos: list | tuple | None = _UNSET,
        execution: Mapping | None = _UNSET,
        env: Mapping[str, str] | None = None,
    ) -> "Workspace":
        """Atomically patch ``<ws>/workspace.json`` and return the workspace.

        ``workdir`` is validated: null bytes and paths resolving inside
        ``<host>/secrets`` or ``<host>/keys`` raise ``ValueError``.
        ``_UNSET`` leaves a field untouched; ``None``/``""`` clears it.
        """
        if not self.workspace_exists(ws_id, env=env):
            raise ValueError(f"unknown workspace id: {ws_id!r}")
        ws = self.workspace(ws_id, env=env)
        meta = _read_meta(ws.dir)
        if label is not None:
            clean_label = label.strip()
            if clean_label:
                meta["label"] = clean_label
        if workdir is not _UNSET:
            if workdir is None or not str(workdir).strip():
                meta.pop("workdir", None)
            else:
                raw = str(workdir).strip()
                if "\x00" in raw:
                    raise ValueError("workdir contains null bytes")
                resolved = _path(raw)
                if _blocked_host_dir(self, resolved):
                    raise ValueError(
                        f"workdir must not resolve inside host secrets/keys: {raw!r}"
                    )
                meta["workdir"] = str(resolved)
        if workdir_lock is not None:
            if workdir_lock:
                meta["workdir_lock"] = True
            else:
                meta.pop("workdir_lock", None)
        if browser_profile is not _UNSET:
            if browser_profile is None or not str(browser_profile).strip():
                meta.pop("browser_profile", None)
            else:
                meta["browser_profile"] = str(browser_profile).strip()
        if sync_policy is not _UNSET:
            if sync_policy is None or not str(sync_policy).strip():
                meta.pop("sync_policy", None)
            else:
                meta["sync_policy"] = _validate_sync_policy(sync_policy)
        if sync_hosts is not _UNSET:
            clean_hosts = _validate_sync_hosts(sync_hosts)
            if not clean_hosts:
                meta.pop("sync_hosts", None)
            else:
                meta["sync_hosts"] = clean_hosts
        if default_host is not _UNSET:
            clean_dhost = _validate_default_host(default_host)
            if not clean_dhost:
                meta.pop("default_host", None)
            else:
                meta["default_host"] = clean_dhost
        if repos is not _UNSET:
            clean_repos = _validate_repos(repos)
            if not clean_repos:
                meta.pop("repos", None)
            else:
                meta["repos"] = [dict(r) for r in clean_repos]
        if execution is not _UNSET:
            if not execution:
                meta.pop("execution", None)
            else:
                clean_exec = _validate_execution(execution)
                # Partial update merges into the stored execution: keys the
                # caller omitted keep their values (an explicit None/""
                # owner is the only clear).
                prev_exec = meta.get("execution")
                merged = (
                    dict(prev_exec) if isinstance(prev_exec, Mapping) else {}
                )
                merged.update(clean_exec)
                if "owner" in execution and "owner" not in clean_exec:
                    merged.pop("owner", None)
                if merged:
                    meta["execution"] = merged
                else:
                    meta.pop("execution", None)
        # Invariant (fail-closed): policy "local" (the default when the
        # field is absent) can never coexist with a sync_hosts allowlist.
        eff_policy = str(meta.get("sync_policy") or "local").strip().lower()
        if eff_policy not in _SYNC_POLICIES:
            eff_policy = "local"
        if eff_policy == "local" and meta.get("sync_hosts"):
            raise ValueError(
                "sync_policy 'local' forbids non-empty sync_hosts; "
                "set sync_policy='synced' or 'shared' explicitly"
            )
        _atomic_json_write(ws.workspace_file, meta)
        return self.workspace(ws_id, env=env)

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
    # ownership / distribution contract (agents-workspace#43, #44):
    # local = never emits ops (fail-closed default); synced = op-log sync
    # to sync_hosts, the hub channel device ids (empty = CLOSED, zero
    # remote devices; NOT host ids -- see _validate_sync_hosts); shared =
    # reserved multi-user.
    sync_policy: str = "local"
    sync_hosts: tuple = ()
    default_host: str = ""  # host-picker default (klanker#133)
    repos: tuple = ()  # [{slug, url, checkout_mode}] — paths stay host-local
    execution: Mapping = field(
        default_factory=dict)  # {owner, failover, lease_sec}
    _policy_error: bool = field(default=False, repr=False, compare=False)
    source_env: Mapping[str, str] = field(
        default_factory=dict, repr=False, compare=False
    )
    apply_overrides: bool = field(default=False, repr=False, compare=False)

    @property
    def policy_error(self) -> bool:
        """True when workspace.json held malformed ownership fields that
        failed closed on read (see ``_ownership_from_meta``)."""
        return self._policy_error

    def is_syncable(self) -> bool:
        """True when this workspace may emit sync ops to remote devices."""
        return self.sync_policy in ("synced", "shared")

    def allows_device(self, device_id: str) -> bool:
        """True when ``device_id`` is explicitly on the sync allowlist.

        Fail-closed: a ``local`` policy or an empty/invalid ``device_id``
        always denies; ``sync_hosts == ()`` permits zero remote devices.
        """
        return bool(
            self.is_syncable()
            and device_id
            and device_id in self.sync_hosts
        )

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
            **_ownership_from_meta(meta),
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
            **_ownership_from_meta(meta),
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
    def store_db(self) -> Path:
        """Workspace-local agents-store SQLite ``<ws>/store.db``.

        Path contract only — never opened here and never file-synced
        (raw ``.db``/``.db-wal``/``.db-shm`` stay local). Secrets never
        live in it; the host vault owns secrets.
        """
        return self.dir / "store.db"

    @property
    def store_exports(self) -> Path:
        """Markdown/projection export dir ``<ws>/exports`` (sync-safe)."""
        return self.dir / "exports"

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

    def _foreign_workdir(self, resolved: Path) -> bool:
        """True when ``resolved`` lives inside another workspace's tree."""
        base = self.host.workspaces_dir
        try:
            inside_pool = resolved == base or resolved.is_relative_to(base)
        except OSError:
            return False
        if not inside_pool:
            return False
        try:
            return not (resolved == self.dir or resolved.is_relative_to(self.dir))
        except OSError:
            return True

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
        # Ownership contract: policy is always written (fail-closed
        # "local" default); local + allowlist can never be persisted.
        meta["sync_policy"] = (
            str(self.sync_policy or "local").strip().lower() or "local"
        )
        if meta["sync_policy"] not in _SYNC_POLICIES:
            meta["sync_policy"] = "local"
        if meta["sync_policy"] == "local" and self.sync_hosts:
            raise ValueError(
                "sync_policy 'local' forbids non-empty sync_hosts; "
                "set sync_policy='synced' or 'shared' explicitly"
            )
        # The workspace object is authoritative for the other four fields:
        # falsy values clear stale keys instead of silently keeping them.
        for key, val in (
            ("sync_hosts", list(self.sync_hosts)),
            ("default_host", self.default_host),
            ("repos", [dict(r) for r in self.repos]),
            ("execution", dict(self.execution)),
        ):
            if val:
                meta[key] = val
            else:
                meta.pop(key, None)
        _atomic_json_write(self.workspace_file, meta)
        return replace(
            self,
            id=meta["id"],
            label=meta["label"],
            default_persona=meta["default_persona"],
            parent_workspace=str(meta.get("parent_workspace") or ""),
            sync_policy=meta["sync_policy"],
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
        meta = _read_meta(self.dir)
        cfg_workdir = str(meta.get("workdir") or "").strip()
        env_workdir = _clean(raw_base, ENV_WORKDIR)
        if workdir:
            workdir_p = _path(str(workdir))
        elif env_workdir and not self._foreign_workdir(_path(env_workdir)):
            # Explicit caller/env workdir always beats workspace.json; a
            # path inside a DIFFERENT workspace tree is a foreign leak and
            # is stripped (C12).
            workdir_p = _path(env_workdir)
        elif cfg_workdir:
            resolved = _path(cfg_workdir)
            if _blocked_host_dir(self.host, resolved):
                warnings.warn(
                    f"workspace {self.id} workdir points inside host "
                    f"secrets/keys and is ignored: {cfg_workdir!r}",
                    UserWarning,
                    stacklevel=2,
                )
                workdir_p = self.workdir
            else:
                workdir_p = resolved
        else:
            workdir_p = self.workdir
        out[ENV_HOST_DIR] = str(self.host.dir)
        out[ENV_WORKSPACE] = self.id
        out[ENV_WORKDIR] = str(workdir_p)
        if meta.get("workdir_lock") is True and "AGENTS_WORKDIR_LOCK" not in out:
            out["AGENTS_WORKDIR_LOCK"] = "1"
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
            ("AGENTS_STORE_DB", None, self.store_db),
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
            "sync_policy": self.sync_policy,
            "sync_hosts": list(self.sync_hosts),
            "default_host": self.default_host,
            "repos": [dict(r) for r in self.repos],
            "execution": dict(self.execution),
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
        host.host_paths,
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


def _agents_keys_did(host: Host) -> str:
    """Best-effort DID lookup via the optional ``agents_keys`` CLI.

    Delegates any key material handling to ``agents_keys``; this process
    never reads raw ``.ed25519`` bytes. The child env is pinned to THIS
    host (``AGENTS_HOST_DIR``/``AGENTS_KEYS_DIR``) so an isolated
    ``Host(tmp)`` can never leak the real ``~/.agents`` host DID.
    Returns "" when the package or a host key is unavailable.
    """
    env = {
        **os.environ,
        ENV_HOST_DIR: str(host.dir),
        "AGENTS_KEYS_DIR": str(host.keys),
    }
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "agents_keys", "did", "host"],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
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


def _reject_secret_config_keys(node: Any, trail: str = "") -> None:
    if isinstance(node, Mapping):
        for key, val in node.items():
            name = str(key)
            if (
                name in INFRA_SECRET_KEYS
                or name in LLM_SECRET_KEYS
                or _SECRETISH_KEY.search(name)
            ):
                raise ValueError(
                    f"secret-like key {trail + name!r} must not be stored "
                    "in <ws>/config.json; use the host vault"
                )
            _reject_secret_config_keys(val, trail + name + ".")


def read_workspace_config(ws: Workspace) -> dict:
    """Read ``<ws>/config.json``: ``{}`` when missing/malformed, ``ValueError``
    on valid JSON that is not an object."""
    try:
        raw = ws.config.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{ws.config} must contain a JSON object")
    return data


def update_workspace_config(
    ws: Workspace, patch: Mapping[str, Any]
) -> dict:
    """Deep-merge ``patch`` into ``<ws>/config.json`` (atomically).

    ``approval.mode`` is validated strictly against
    ``ALLOWED_APPROVAL_MODES`` (empty clears it). Secret-looking keys are
    rejected: ``config.json`` holds non-secret policy only.
    """
    if not isinstance(patch, Mapping):
        raise ValueError("config patch must be a mapping")
    _reject_secret_config_keys(patch)
    approval = patch.get("approval")
    if isinstance(approval, Mapping) and "mode" in approval:
        mode = approval.get("mode")
        if mode not in (None, "") and mode not in ALLOWED_APPROVAL_MODES:
            raise ValueError(
                f"invalid approval.mode {mode!r}; "
                f"allowed: {', '.join(ALLOWED_APPROVAL_MODES)}"
            )
    merged = read_workspace_config(ws)

    def _merge(dst: dict, src: Mapping) -> None:
        for key, val in src.items():
            if isinstance(val, Mapping) and isinstance(dst.get(key), dict):
                _merge(dst[key], val)
            elif val is None or val == "":
                dst.pop(key, None)
            else:
                dst[key] = val

    _merge(merged, patch)
    _atomic_json_write(ws.config, merged)
    return merged


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
