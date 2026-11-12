"""Runtime boundary for workspaces: local subprocess or per-ws container.

A workspace carries an ``isolation`` field in ``workspace.json``:
``local`` (default) spawns a plain subprocess — cwd-jail semantics only.
``container`` runs turns inside a per-workspace container that sees only the
workspace dir mounted at ``/ws``; other workspaces do not exist inside.

The container is a dumb compute shell (``sleep infinity`` keep-alive, turns via
``docker exec``), never a server. The relay stays the only entry point.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Mapping

from .workspace import Workspace

LABEL = "klanker-ws"
ENV_ISOLATION = "AGENTS_WS_ISOLATION"
ENV_IMAGE = "AGENTS_WS_IMAGE"
ENV_TTL = "AGENTS_WS_IDLE_TTL"
DEFAULT_IMAGE = "klanker:latest"
CONTAINER_WS = "/ws"

# ws.id -> monotonic timestamp of last spawned turn (warm-pool bookkeeping).
_LAST_USE: dict[str, float] = {}

# Host env prefixes forwarded into ws containers: suite config, LLM keys,
# git/GitHub auth, proxies. Values travel via the docker client env
# (``-e KEY``), never as ``-e KEY=VAL`` on the exec cmdline.
ENV_PASSTHROUGH_PREFIXES = (
    "AGENTS_",
    "KLANKER_",
    "LLM_",
    "OPENAI_",
    "OPENROUTER_",
    "ANTHROPIC_",
    "LMSTUDIO_",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GIT_",
    "RELAY_",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "TZ",
    "LANG",
)


def _passthrough_keys(host_env: Mapping[str, str]) -> list[str]:
    return [
        k for k in host_env
        if any(k.startswith(p) for p in ENV_PASSTHROUGH_PREFIXES)
    ]


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return env if env is not None else os.environ


def isolation_of(ws: Workspace, env: Mapping[str, str] | None = None) -> str:
    """Effective isolation: manifest > AGENTS_WS_ISOLATION > local."""
    mode = (ws.isolation or _env(env).get(ENV_ISOLATION, "")).strip().lower()
    return "container" if mode == "container" else "local"


def docker_bin() -> str | None:
    """docker CLI path or the socket-proxy'd shim name; None when absent."""
    return shutil.which("docker")


def image_for(ws: Workspace, env: Mapping[str, str] | None = None) -> str:
    """Explicit ws/env image wins; inside docker, default to the relay's
    own image so ws containers run the same suite without a named tag."""
    explicit = ws.image or _env(env).get(ENV_IMAGE, "").strip()
    if explicit:
        return explicit
    own = _own_image()
    return own or DEFAULT_IMAGE


def _own_image() -> str:
    """Config.Image of the container this process runs in (hostname=id)."""
    if docker_bin() is None:
        return ""
    try:
        out = _docker(
            "inspect", "-f", "{{.Config.Image}}", os.environ.get("HOSTNAME", "")
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return ""


def _container_name(ws: Workspace) -> str:
    return f"{LABEL}-{ws.id}"


def _docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [docker_bin() or "docker", *args],
        capture_output=True,
        text=True,
    )


def ensure_container(
    ws: Workspace,
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """Return the running warm container name for ``ws``.

    Mounts only ``ws.dir`` at ``/ws`` — the workspace boundary is honest:
    inside the container no other workspace data exists. Loud failure when
    docker is unavailable (no silent local fallback)."""
    if docker_bin() is None:
        raise RuntimeError(
            f"workspace {ws.id!r} requires isolation=container but no "
            "docker binary is available"
        )
    name = _container_name(ws)
    probe = _docker("inspect", "-f", "{{.State.Running}}", name)
    if probe.returncode == 0:
        if probe.stdout.strip() == "true":
            return name
        started = _docker("start", name)
        if started.returncode == 0:
            return name
        _docker("rm", "-f", name)
    workdir = ws.workdir if ws.workdir.is_dir() else ws.dir
    run = _docker(
        "run", "-d",
        "--name", name,
        "--label", f"{LABEL}={ws.id}",
        "-v", f"{_host_mount(ws, env)}:{CONTAINER_WS}",
        "-w", _to_container_path(ws, _posix(workdir)),
        image_for(ws, env),
        "sleep", "infinity",
    )
    if run.returncode != 0:
        raise RuntimeError(
            f"container spawn failed for workspace {ws.id!r}: "
            f"{run.stderr.strip() or run.stdout.strip()}"
        )
    return name


def _posix(path) -> str:
    return str(path).replace("\\", "/")


ENV_HOST_MAP = "AGENTS_WS_HOST_DIR"


def _host_mount(ws: Workspace, env: Mapping[str, str] | None) -> str:
    """Host-side path for the ws mount.

    Inside a container the ws dir lives under a docker volume path that
    differs from the daemon's view. ``AGENTS_WS_HOST_DIR`` names the
    host-side equivalent of ``AGENTS_HOST_DIR`` so
    ``<host_dir>/workspaces/<id>`` resolves to a real host path for
    ``docker run -v``. Without it the container path is used as-is
    (correct when relay and daemon share the fs view)."""
    root = _posix(ws.dir)
    host_map = _env(env).get(ENV_HOST_MAP, "").strip()
    host_dir = _env(env).get("AGENTS_HOST_DIR", "").strip()
    if host_map and host_dir and root.startswith(_posix(host_dir)):
        return host_map.rstrip("/") + root[len(_posix(host_dir)):]
    return root


def _docker_env_flags(env: Mapping[str, str]) -> list[str]:
    """``-e KEY`` per var — docker takes the value from the client env,
    keeping secrets out of host process listings."""
    flags: list[str] = []
    for key in env:
        flags += ["-e", key]
    return flags


def spawn(
    ws: Workspace,
    argv: list[str],
    env: Mapping[str, str] | None = None,
    *,
    pipes: bool = False,
) -> subprocess.Popen:
    """Spawn ``argv`` under the workspace boundary.

    ``local``: plain subprocess with the merged env (status quo).
    ``container``: ``docker exec -i`` into the ws warm container, env
    passthrough via ``-e`` flags (secrets never hit ws files).
    ``pipes=True`` wires stdout/stderr through PIPE for stream readers."""
    child_env = dict(os.environ)
    child_env.update(env or {})
    kw = (
        {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
         "text": True, "bufsize": 1}
        if pipes
        else {}
    )
    if isolation_of(ws, env) != "container":
        return subprocess.Popen(argv, env=child_env, **kw)
    name = ensure_container(ws, env=env)
    keys = _passthrough_keys(child_env)
    cmd = [docker_bin() or "docker", "exec", "-i"]
    cmd += _docker_env_flags({k: "" for k in keys})
    cmd += [name, *argv]
    _LAST_USE[ws.id] = time.monotonic()
    if os.name != "nt":
        kw["start_new_session"] = True
    return subprocess.Popen(cmd, env=child_env, **kw)


def sweep(env: Mapping[str, str] | None = None) -> list[str]:
    """Remove stale ``klanker-ws`` containers (crash orphans). Returns ids."""
    if docker_bin() is None:
        return []
    out = _docker("ps", "-aq", "--filter", f"label={LABEL}")
    ids = [line for line in out.stdout.split() if line]
    if ids:
        _docker("rm", "-f", *ids)
    return ids


def reap_idle(env: Mapping[str, str] | None = None) -> list[str]:
    """Stop warm containers idle past AGENTS_WS_IDLE_TTL seconds (default 900)."""
    ttl = float(_env(env).get(ENV_TTL, "900") or 900)
    now = time.monotonic()
    stopped: list[str] = []
    for ws_id, last in list(_LAST_USE.items()):
        if now - last < ttl:
            continue
        _docker("stop", f"{LABEL}-{ws_id}")
        stopped.append(ws_id)
        del _LAST_USE[ws_id]
    return stopped


def run(
    ws: Workspace,
    argv: list[str],
    env: Mapping[str, str] | None = None,
    *,
    capture: bool = True,
    timeout: int = 0,
) -> subprocess.CompletedProcess:
    """Blocking variant of spawn(): run argv under the ws boundary."""
    child_env = dict(os.environ)
    child_env.update(env or {})
    if isolation_of(ws, env) != "container":
        return subprocess.run(
            argv, capture_output=capture, text=True,
            env=child_env, timeout=timeout or None,
        )
    name = ensure_container(ws, env=env)
    keys = _passthrough_keys(child_env)
    cmd = [docker_bin() or "docker", "exec", "-i"]
    cmd += _docker_env_flags({k: "" for k in keys})
    cmd += [name, *argv]
    _LAST_USE[ws.id] = time.monotonic()
    return subprocess.run(
        cmd, capture_output=capture, text=True,
        env=child_env, timeout=timeout or None,
    )


def _to_container_path(ws: Workspace, value: str) -> str:
    """Map a host path under ``ws.dir`` to the container mount ``/ws``."""
    raw = _posix(value or "")
    root = _posix(ws.dir).rstrip("/")
    if raw == root:
        return CONTAINER_WS
    if raw.startswith(root + "/"):
        return CONTAINER_WS + raw[len(root):]
    return raw


def to_container_env(
    ws: Workspace, env: Mapping[str, str] | None
) -> dict[str, str]:
    """ws.env() values rewritten for the container view (host ws.dir -> /ws)."""
    return {k: _to_container_path(ws, v) for k, v in (env or {}).items()}


def to_container_argv(ws: Workspace, argv: list[str]) -> list[str]:
    return [_to_container_path(ws, a) for a in argv]


def describe(ws: Workspace, env: Mapping[str, str] | None = None) -> str:
    mode = isolation_of(ws, env)
    if mode != "container":
        return "local"
    return f"container:{image_for(ws, env)}"
