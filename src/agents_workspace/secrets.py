"""Host-only secret vault, precedence resolver, and audit helpers."""

from __future__ import annotations

import json
import os
import re
import secrets as _py_secrets
from pathlib import Path
from typing import Any, Mapping

from .workspace import (
    ENV_WORKSPACE,
    INFRA_SECRET_KEYS,
    Host,
    Workspace,
    _json_dict,
    default_host_dir,
    host_config,
)

ENV_SECRETS_ORDER = "AGENTS_SECRETS_ORDER"
ENV_KEYRING = "AGENTS_KEYRING"
_KEY_OK = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_EXPAND_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_RUN_SECRETS_DIR = Path("/run/secrets")
_SERVICE_HOST = "agents-host"
_SERVICE_WS_PREFIX = "agents-ws-"
_SCOPES = frozenset({"auto", "host", "workspace"})


def secrets_dir(host: Host) -> Path:
    """Host-owned vault directory ``<host>/secrets``."""
    return host.dir / "secrets"


def secrets_host_file(host: Host) -> Path:
    """Host-wide secrets file ``<host>/secrets/host.json``."""
    return secrets_dir(host) / "host.json"


def secrets_ws_file(ws: Workspace) -> Path:
    """Workspace-scoped host vault file ``<host>/secrets/ws-<id>.json``."""
    return secrets_dir(ws.host) / f"ws-{ws.id}.json"


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _atomic_write_json(path: Path, data: Mapping[str, str]) -> Path:
    _ensure_private_dir(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{_py_secrets.token_hex(4)}.tmp")
    payload = json.dumps(dict(sorted(data.items())), indent=2) + "\n"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return path


def _validate_key(key: str) -> str:
    clean = (key or "").strip()
    if not clean or not _KEY_OK.match(clean):
        raise ValueError(f"invalid secret key name: {key!r}")
    return clean


def _read_run_secret(key: str) -> str:
    root = _RUN_SECRETS_DIR
    if not root.is_dir():
        return ""
    for candidate in (key, key.lower()):
        path = root / candidate
        try:
            if path.is_file():
                val = path.read_text(encoding="utf-8").rstrip("\r\n")
                if val:
                    return val
        except OSError:
            continue
    return ""


def _keyring_enabled(host: Host, env: Mapping[str, str]) -> bool:
    flag = str(env.get(ENV_KEYRING) or "").strip()
    if flag == "0":
        return False
    if flag == "1":
        return True
    # Only query the live OS keyring automatically when using default ~/.agents
    # so temp AGENTS_HOST_DIR runs never read or pollute real credentials.
    return host.dir == default_host_dir()


def _keyring_get(service: str, key: str, host: Host, env: Mapping[str, str]) -> str:
    if not _keyring_enabled(host, env):
        return ""
    try:
        import keyring  # type: ignore[import-not-found]

        val = keyring.get_password(service, key)
        return val if isinstance(val, str) and val else ""
    except Exception:
        return ""


def _read_key_from_json(path: Path, key: str) -> str:
    val = _json_dict(path).get(key)
    return val if isinstance(val, str) and val else ""


def _select_existing_ws(host: Host, env: Mapping[str, str]) -> Workspace | None:
    """Resolve existing workspace on ``host`` without creating one implicitly."""
    if str(env.get(ENV_WORKSPACE) or "").strip():
        try:
            return host.select_workspace(env=env)
        except ValueError:
            return None
    if not host.list_workspaces(env=env):
        return None
    try:
        return host.select_workspace(env=env)
    except ValueError:
        return None


def _resolve_target(
    *,
    ws: Workspace | None,
    host: Host | None,
    env: Mapping[str, str],
    need_ws: bool,
) -> tuple[Host, Workspace | None]:
    if ws is not None:
        return (host or ws.host), ws
    target_host = host or Host.from_env(env)
    if not need_ws:
        return target_host, None
    if host is not None:
        return target_host, _select_existing_ws(target_host, env)
    try:
        selected = Workspace.from_env(env)
        return selected.host, selected
    except ValueError:
        return target_host, _select_existing_ws(target_host, env)


def resolve_secret(
    key: str,
    ws: Workspace | None = None,
    env: Mapping[str, str] | None = None,
    *,
    scope: str = "auto",
    host: Host | None = None,
) -> str:
    """Resolve ``key`` from env, ``/run/secrets``, optional keyring, and host vault.

    Host-infra keys (``INFRA_SECRET_KEYS``) and ``scope="host"`` ALWAYS follow
    ``ENV > /run/secrets > keyring > host.json > legacy <host>/secrets.json``
    and never read workspace secret files regardless of ``AGENTS_SECRETS_ORDER``.

    Workspace/provider secrets default to
    ``ws-<id>.json > legacy <ws>/secrets.json > keyring > host.json > ENV``,
    flipped to ``ENV > /run/secrets > ws-<id>.json > ...`` when
    ``AGENTS_SECRETS_ORDER=env-first``.
    """
    if scope not in _SCOPES:
        raise ValueError(f"invalid secret scope: {scope!r}")
    env_map = os.environ if env is None else env
    infra_only = scope == "host" or key in INFRA_SECRET_KEYS
    target_host, target_ws = _resolve_target(
        ws=ws, host=host, env=env_map, need_ws=not infra_only
    )

    if infra_only:
        env_val = str(env_map.get(key) or "")
        if env_val:
            return env_val
        run_val = _read_run_secret(key)
        if run_val:
            return run_val
        kr_val = _keyring_get(_SERVICE_HOST, key, target_host, env_map)
        if kr_val:
            return kr_val
        for path in (secrets_host_file(target_host), target_host.secrets):
            val = _read_key_from_json(path, key)
            if val:
                return val
        return ""

    order = str(env_map.get(ENV_SECRETS_ORDER) or "").strip().lower()
    if order == "env-first":
        env_val = str(env_map.get(key) or "")
        if env_val:
            return env_val
        run_val = _read_run_secret(key)
        if run_val:
            return run_val

    if target_ws is not None:
        for path in (secrets_ws_file(target_ws), target_ws.secrets):
            val = _read_key_from_json(path, key)
            if val:
                return val
        kr_ws = _keyring_get(
            f"{_SERVICE_WS_PREFIX}{target_ws.id}", key, target_host, env_map
        )
        if kr_ws:
            return kr_ws

    kr_host = _keyring_get(_SERVICE_HOST, key, target_host, env_map)
    if kr_host:
        return kr_host

    for path in (secrets_host_file(target_host), target_host.secrets):
        val = _read_key_from_json(path, key)
        if val:
            return val

    if order != "env-first":
        env_val = str(env_map.get(key) or "")
        if env_val:
            return env_val
        run_val = _read_run_secret(key)
        if run_val:
            return run_val
    return ""


def _target_vault_file(
    *,
    scope: str,
    host: Host | None,
    ws: Workspace | None,
    env: Mapping[str, str] | None,
) -> Path:
    if scope not in ("host", "workspace"):
        raise ValueError(f"write/delete scope must be 'host' or 'workspace', got {scope!r}")
    env_map = os.environ if env is None else env
    if scope == "host":
        target_host = host or (ws.host if ws is not None else Host.from_env(env_map))
        return secrets_host_file(target_host)
    if ws is not None:
        target_ws = ws
    elif host is not None:
        target_ws = host.select_workspace(env=env_map)
    else:
        target_ws = Workspace.from_env(env_map)
    return secrets_ws_file(target_ws)


def write_secret(
    key: str,
    value: str,
    *,
    host: Host | None = None,
    ws: Workspace | None = None,
    scope: str = "host",
    env: Mapping[str, str] | None = None,
) -> Path:
    """Write ``key`` -> ``value`` into host-side vault JSON with ``0600`` mode."""
    clean_key = _validate_key(key)
    if not isinstance(value, str) or not value:
        raise ValueError("secret value must be a non-empty string")
    if scope == "workspace" and clean_key in INFRA_SECRET_KEYS:
        raise ValueError(f"infra secret {clean_key!r} must be stored in host scope")
    path = _target_vault_file(scope=scope, host=host, ws=ws, env=env)
    existing = {
        k: v for k, v in _json_dict(path).items() if isinstance(v, str) and v
    }
    existing[clean_key] = value
    return _atomic_write_json(path, existing)


def delete_secret(
    key: str,
    *,
    host: Host | None = None,
    ws: Workspace | None = None,
    scope: str = "host",
    env: Mapping[str, str] | None = None,
) -> bool:
    """Delete ``key`` from the targeted host vault file. Returns True if removed."""
    clean_key = _validate_key(key)
    path = _target_vault_file(scope=scope, host=host, ws=ws, env=env)
    if not path.is_file():
        return False
    existing = {
        k: v for k, v in _json_dict(path).items() if isinstance(v, str) and v
    }
    if clean_key not in existing:
        return False
    del existing[clean_key]
    _atomic_write_json(path, existing)
    return True


def secret_inventory(
    *,
    host: Host | None = None,
    ws: Workspace | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, list[str]]:
    """Return sorted key names per vault bucket (never secret values)."""
    env_map = os.environ if env is None else env
    target_host, target_ws = _resolve_target(
        ws=ws, host=host, env=env_map, need_ws=True
    )
    out: dict[str, list[str]] = {
        "host": sorted(k for k, v in _json_dict(secrets_host_file(target_host)).items() if v),
        "workspace": (
            sorted(k for k, v in _json_dict(secrets_ws_file(target_ws)).items() if v)
            if target_ws is not None
            else []
        ),
        "legacy_host": sorted(k for k, v in _json_dict(target_host.secrets).items() if v),
        "legacy_workspace": (
            sorted(k for k, v in _json_dict(target_ws.secrets).items() if v)
            if target_ws is not None
            else []
        ),
    }
    return out


def list_secret_names(
    *,
    host: Host | None = None,
    ws: Workspace | None = None,
    scope: str = "all",
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Return sorted secret key names for ``scope`` (``host``, ``workspace``, ``all``)."""
    if scope not in ("all", "host", "workspace"):
        raise ValueError(f"invalid list scope: {scope!r}")
    inv = secret_inventory(host=host, ws=ws, env=env)
    names: set[str] = set()
    if scope in ("all", "host"):
        names.update(inv["host"])
        names.update(inv["legacy_host"])
    if scope in ("all", "workspace"):
        names.update(inv["workspace"])
        names.update(inv["legacy_workspace"])
    return sorted(names)


def expand_secrets(
    obj: Any,
    *,
    ws: Workspace | None = None,
    host: Host | None = None,
    env: Mapping[str, str] | None = None,
    trusted: bool = False,
) -> Any:
    """Recursively expand ``${VAR}`` / ``${VAR:-default}`` ONLY when ``trusted=True``.

    Untrusted inputs (such as workspace-local ``mcp.json``) are returned untouched.
    """
    if not trusted:
        return obj
    if isinstance(obj, str):
        def _sub(match: re.Match[str]) -> str:
            var, default = match.group(1), match.group(2)
            val = resolve_secret(var, ws=ws, host=host, env=env)
            if val:
                return val
            return default if default is not None else ""

        return _EXPAND_RE.sub(_sub, obj)
    if isinstance(obj, list):
        return [expand_secrets(item, ws=ws, host=host, env=env, trusted=True) for item in obj]
    if isinstance(obj, tuple):
        return tuple(expand_secrets(item, ws=ws, host=host, env=env, trusted=True) for item in obj)
    if isinstance(obj, dict):
        return {
            str(k): expand_secrets(v, ws=ws, host=host, env=env, trusted=True)
            for k, v in obj.items()
        }
    return obj


def _add_issue(
    issues: list[dict[str, str]],
    severity: str,
    path: Path,
    message: str,
) -> None:
    issues.append({"severity": severity, "path": str(path), "message": message})


def _check_perms(issues: list[dict[str, str]], path: Path, expect_mask: int) -> None:
    if os.name == "nt" or not path.exists():
        return
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        return
    if mode & ~expect_mask & 0o077:
        _add_issue(
            issues,
            "high",
            path,
            f"overly permissive mode {oct(mode)} (expected <= {oct(expect_mask)})",
        )


def doctor(
    host: Host | None = None, *, env: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Audit host/workspace secret hygiene, permissions, and legacy state."""
    env_map = os.environ if env is None else env
    target = host or Host.from_env(env_map)
    issues: list[dict[str, str]] = []

    if target.has_legacy_layout():
        _add_issue(
            issues,
            "medium",
            target.dir,
            "legacy host layout detected; run 'agents-workspace migrate'",
        )

    sdir = secrets_dir(target)
    if sdir.exists():
        _check_perms(issues, sdir, 0o700)
        for p in sorted(sdir.glob("*.json")):
            _check_perms(issues, p, 0o600)

    if target.keys.exists():
        _check_perms(issues, target.keys, 0o700)
        for p in sorted(target.keys.glob("*.ed25519")):
            _check_perms(issues, p, 0o600)

    host_voice = target.dir / "voice.json"
    if host_voice.is_file() and _json_dict(host_voice).get("keys"):
        _add_issue(
            issues,
            "high",
            host_voice,
            "plaintext voice API keys in voice.json; move to host vault",
        )

    if target.secrets.is_file() and _json_dict(target.secrets):
        _add_issue(
            issues,
            "medium",
            target.secrets,
            "legacy host secrets.json present; migrate to secrets/host.json",
        )

    workspaces = target.list_workspaces(env=env_map)
    if len(workspaces) > 1:
        configured = str(host_config(target).get("default_workspace") or "").strip()
        if not configured and not target.workspace_exists("default", env=env_map):
            _add_issue(
                issues,
                "medium",
                target.agents_json,
                "multiple workspaces exist with no default_workspace set",
            )

    for ws in workspaces:
        if ws.secrets.is_file() and _json_dict(ws.secrets):
            _add_issue(
                issues,
                "high",
                ws.secrets,
                "workspace secrets.json inside workspace tree; move to host vault",
            )
        dotenv = ws.dir / ".env"
        if dotenv.is_file():
            _add_issue(issues, "high", dotenv, "plaintext .env inside workspace tree")
        if ws.calendar.is_file() and _json_dict(ws.calendar).get("password"):
            _add_issue(
                issues,
                "high",
                ws.calendar,
                "plaintext CalDAV password in calendar.json",
            )
        if ws.sync_config.is_file() and _json_dict(ws.sync_config).get("token"):
            _add_issue(
                issues,
                "high",
                ws.sync_config,
                "plaintext sync token in sync_config.json",
            )
        voice_cfg = ws.dir / "voice.json"
        if voice_cfg.is_file() and _json_dict(voice_cfg).get("keys"):
            _add_issue(
                issues,
                "high",
                voice_cfg,
                "plaintext voice API keys in voice.json",
            )
        if ws.mcp_json.is_file():
            data = _json_dict(ws.mcp_json)
            servers = data.get("mcpServers") if isinstance(data.get("mcpServers"), dict) else {}
            if any(
                isinstance(cfg, dict) and cfg.get("env")
                for cfg in servers.values()
            ):
                _add_issue(
                    issues,
                    "medium",
                    ws.mcp_json,
                    "workspace mcp.json defines env entries; move server config to host mcp/",
                )
        for rel in ("vapid.json", "push.json", "remotes.json"):
            rpath = ws.relay_state / rel
            if rpath.is_file():
                _add_issue(
                    issues,
                    "medium",
                    rpath,
                    f"legacy relay credential/remote file in workspace: {rel}",
                )

    if target.dir == default_host_dir():
        legacy_klanker = Path.home() / ".klanker"
        if legacy_klanker.exists():
            _add_issue(
                issues,
                "info",
                legacy_klanker,
                "legacy ~/.klanker directory present; migrate/retire in Wave 3",
            )

    return {
        "host": str(target.dir),
        "status": "warn" if issues else "ok",
        "issues": issues,
    }
