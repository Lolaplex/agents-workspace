"""One-shot host migration: legacy ``~/.agents`` layout -> Host/Workspace.

Planner is pure; executor takes a lock, moves entries, and appends one JSON
log per run under ``<host>/logs/``. ``--rollback <log>`` reverses it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from .workspace import DIR_DOMAINS, Host, Workspace

# Legacy entries that belong to a workspace's data tree.
_WS_FILES = (
    "calendar.json",
    "store.db",
    "workspace.json",
    ".tombstones.json",
    "sync_config.json",
    "AGENTS.md",
    "CLAUDE.md",
    ".env",
)

# Legacy entries that stay on (or move to) host infrastructure.
_HOST_DIRS = ("keys", "run", "cache", "logs", "clients", "browser")
_HOST_FILES = (
    "secrets.json",
    "agents.json",
    "host_paths.json",
    "users.json",
    "identity.json",
    "mcp.json",
    "known-dids.jsonl",
)
# Legacy browser/chromium profile dirs stay host-level (they are profiles,
# not the new <ws>/browser artifacts domain).
_PROFILE_PREFIXES = ("browser-", "browser_", "chromium")
# Legacy dirs that move under a host dir instead of staying top-level.
_HOST_DIR_REMAP = {"repl": "clients"}
# Legacy dirs that merge into a workspace domain (old aliases for the domain).
_WS_DIR_REMAP = {"harness-content": "content"}
# Legacy host files that move into a host subdir.
_HOST_FILE_REMAP = {
    "mcp-tools-cache.json": "cache",
}
# Host files whose destination may already exist and which are regenerable;
# a stale legacy copy is deleted rather than merged or flagged.
_REGENERABLE = {"mcp-tools-cache.json"}

_EXT_WS_GLOBS = (".agents-workspaces", ".agents-relay", ".client", ".klanker")


@dataclass
class Move:
    src: Path
    dst: Path
    kind: str  # "ws" | "host" | "external" | "delete" | "merge-users"
    # populated during execution for kind == "merge-users"
    merge_src_backup: str = ""
    merge_backup: str = ""

    def to_dict(self) -> dict:
        d = {"src": str(self.src), "dst": str(self.dst), "kind": self.kind}
        if self.kind == "merge-users":
            d["merge_src_backup"] = self.merge_src_backup
            d["merge_backup"] = self.merge_backup
        return d


def _merge_users_file(m: Move) -> None:
    """Union-merge legacy identity.json (src) into users.json (dst).

    Existing dst rows win on key collision. Both original file bodies are
    kept on the Move so the run log supports a byte-exact rollback.
    """
    src_body = m.src.read_text(encoding="utf-8")
    dst_body = m.dst.read_text(encoding="utf-8")
    m.merge_src_backup = src_body
    m.merge_backup = dst_body
    src = json.loads(src_body)
    dst = json.loads(dst_body)
    src_users = src.get("users") if isinstance(src, dict) else None
    dst_users = dst.get("users") if isinstance(dst, dict) else None
    if not isinstance(src_users, dict) or not isinstance(dst_users, dict):
        raise RuntimeError(f"cannot merge users: bad schema in {m.src} or {m.dst}")
    for key, row in src_users.items():
        dst_users.setdefault(key, row)
    tmp = m.dst.with_suffix(m.dst.suffix + ".tmp")
    tmp.write_text(json.dumps(dst, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, m.dst)
    m.src.unlink()


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                text=True, encoding="utf-8", errors="replace",
            )
        except Exception:
            return False
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def plan_migration(host: Host, ws_id: str = "default",
                   home: Path | None = None) -> list[Move]:
    """Pure plan: legacy host dir + external dirs -> new layout."""
    moves: list[Move] = []
    host_dir = host.dir
    ws_dir = host_dir / "workspaces" / ws_id
    if not host_dir.is_dir():
        return moves

    # 1. workspace domain dirs (legacy top-level "browser" is a profile, not
    # the <ws>/browser artifacts domain -> handled with host dirs below)
    for name in DIR_DOMAINS:
        if name == "browser":
            continue
        src = host_dir / name
        if src.is_dir():
            moves.append(Move(src, ws_dir / name, "ws"))
    # 2. workspace files
    for name in _WS_FILES:
        src = host_dir / name
        if src.is_file():
            moves.append(Move(src, ws_dir / name, "ws"))
    # 3. host infra (identity.json -> users.json, known-dids -> keys/)
    for name in _HOST_DIRS:
        src = host_dir / name
        if src.is_dir():
            moves.append(Move(src, host_dir / name, "host"))
    # legacy aliases for ws domain dirs merge their children into the domain
    for name, domain in _WS_DIR_REMAP.items():
        src = host_dir / name
        if not src.is_dir():
            continue
        dst_dir = ws_dir / domain
        for child in sorted(src.iterdir()):
            dst = dst_dir / child.name
            if dst.exists() and child.is_file() and dst.is_file() \
                    and child.read_bytes() == dst.read_bytes():
                moves.append(Move(child, Path(""), "delete"))
            else:
                moves.append(Move(child, dst, "ws"))
        moves.append(Move(src, Path(""), "delete"))
    for name, sub in _HOST_DIR_REMAP.items():
        src = host_dir / name
        if src.is_dir():
            moves.append(Move(src, host_dir / sub / name, "host"))
    for entry in sorted(host_dir.iterdir()):
        if entry.is_dir() and entry.name.startswith(_PROFILE_PREFIXES):
            moves.append(Move(entry, entry, "host"))
    for name in _HOST_FILES:
        src = host_dir / name
        if not src.is_file():
            continue
        if name == "identity.json":
            dst = host_dir / "users.json"
            kind = "merge-users" if dst.is_file() else "host"
        elif name == "known-dids.jsonl":
            dst = host_dir / "keys" / name
            kind = "host"
        else:
            dst = host_dir / name
            kind = "host"
        moves.append(Move(src, dst, kind))
    for name, sub in _HOST_FILE_REMAP.items():
        src = host_dir / name
        if not src.is_file():
            continue
        dst = host_dir / sub / name
        if name in _REGENERABLE and dst.is_file():
            moves.append(Move(src, Path(""), "delete"))
        else:
            moves.append(Move(src, dst, "host"))
    prompt = host_dir / "klanker_prompt.txt"
    if prompt.is_file():
        moves.append(
            Move(prompt, host_dir / "run" / f"klanker_prompt_{ws_id}.txt", "host"))

    # 4. external roots
    home = home or Path.home()
    for pattern in _EXT_WS_GLOBS:
        ext = home / pattern
        if pattern == ".agents-workspaces" and ext.is_dir():
            for child in sorted(ext.iterdir()):
                if child.is_dir():
                    moves.append(
                        Move(child, host_dir / "workspaces" / child.name, "external"))
        elif pattern == ".agents-relay" and ext.is_dir():
            moves.append(Move(ext, ws_dir / "relay", "external"))
        elif pattern == ".client" and ext.is_dir():
            moves.append(Move(ext, host_dir / "clients", "external"))
        elif pattern == ".klanker" and ext.is_dir():
            pid_file = ext / "daemon.pid"
            if pid_file.is_file():
                try:
                    pid = int(pid_file.read_text().strip())
                except (OSError, ValueError):
                    pid = -1
                if pid <= 0 or not _pid_alive(pid):
                    moves.append(Move(pid_file, Path(""), "delete"))

    # 5. catch-all: any other top-level entry is workspace data
    planned_srcs = {m.src for m in moves}
    for entry in sorted(host_dir.iterdir()):
        if entry.name == "workspaces" or entry in planned_srcs:
            continue
        moves.append(Move(entry, ws_dir / entry.name, "ws"))

    # no-op moves (src already lives at dst on this host)
    moves = [m for m in moves
             if m.kind == "delete" or _norm(m.src) != _norm(m.dst)]
    return moves


def _norm(path: Path) -> str:
    return os.path.normcase(str(path.expanduser().resolve()))


def _conflicts(moves: Iterable[Move]) -> list[Move]:
    moves = list(moves)
    skip = ("delete", "merge-users")
    out = [m for m in moves if m.kind not in skip and m.dst.exists()]
    seen: dict[str, Move] = {}
    for m in moves:
        if m.kind in skip:
            continue
        key = _norm(m.dst)
        if key in seen:
            out.append(m)
        seen[key] = m
    return out


def already_migrated(host: Host) -> bool:
    """No legacy markers and a workspaces/ tree exists."""
    return (host.dir / "workspaces").is_dir() and not any(
        (host.dir / name).exists()
        for name in (*DIR_DOMAINS, *_WS_FILES, "identity.json", "known-dids.jsonl")
    )


def _lock(host: Host):
    host.run.mkdir(parents=True, exist_ok=True)
    lock_path = host.run / "migrate.lock"
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError(f"migration lock held: {lock_path}")
    return fd, lock_path


def execute_migration(moves: list[Move], host: Host, *,
                      dry_run: bool = False) -> Optional[Path]:
    """Apply the plan. Returns the JSON log path (or None for dry-run)."""
    conflicts = _conflicts(moves)
    if conflicts:
        lines = "\n".join(f"  {m.src} -> {m.dst} (exists)" for m in conflicts)
        raise RuntimeError(f"destination exists, aborting before any move:\n{lines}")
    if dry_run or not moves:
        return None

    fd, lock_path = _lock(host)
    try:
        host.logs.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        log_path = host.logs / f"migrate-{stamp}.json"
        done: list[Move] = []
        try:
            for m in moves:
                if m.kind == "delete":
                    if m.src.is_dir():
                        m.src.rmdir()
                    else:
                        m.src.unlink(missing_ok=True)
                    done.append(m)
                    continue
                if m.kind == "merge-users":
                    _merge_users_file(m)
                    done.append(m)
                    continue
                m.dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(m.src), str(m.dst))
                done.append(m)
        except Exception:
            # best-effort reverse what already moved, then re-raise
            for m in reversed(done):
                try:
                    if m.kind == "delete":
                        continue
                    if m.kind == "merge-users":
                        if m.merge_backup:
                            Path(m.dst).write_text(m.merge_backup, encoding="utf-8")
                            Path(m.src).write_text(m.merge_src_backup, encoding="utf-8")
                        continue
                    m.src.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(m.dst), str(m.src))
                except OSError:
                    pass
            raise
        log_path.write_text(
            json.dumps(
                {"host": str(host.dir), "moves": [m.to_dict() for m in done]},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return log_path
    finally:
        os.close(fd)
        lock_path.unlink(missing_ok=True)


def rollback(log_path: Path) -> int:
    """Reverse a migration log. Returns the number of restored entries."""
    data = json.loads(Path(log_path).read_text(encoding="utf-8"))
    restored = 0
    for m in reversed(data.get("moves") or []):
        src = Path(m["src"])
        dst = Path(m["dst"])
        if m["kind"] == "delete":
            continue  # cannot un-delete a dead pid file; it was stale anyway
        if m["kind"] == "merge-users":
            if m.get("merge_backup"):
                dst.write_text(m["merge_backup"], encoding="utf-8")
            if m.get("merge_src_backup") is not None:
                src.write_text(m["merge_src_backup"], encoding="utf-8")
            restored += 1
            continue
        if not dst.exists():
            continue
        src.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dst), str(src))
        restored += 1
    return restored


def run_cli(host: Host, *, ws_id: str = "default", dry_run: bool = False,
            yes: bool = False, home: Path | None = None) -> int:
    if already_migrated(host):
        print("already migrated")
        return 0
    moves = plan_migration(host, ws_id=ws_id, home=home)
    if not moves:
        print("nothing to migrate")
        return 0
    verb = "would move" if dry_run else "move"
    for m in moves:
        if m.kind == "delete":
            print(f"{verb} (delete stale) {m.src}")
        elif m.kind == "merge-users":
            print(f"{verb} (merge users) {m.src} -> {m.dst}")
        else:
            print(f"{verb} {m.src} -> {m.dst}")
    conflicts = _conflicts(moves)
    if conflicts:
        print("conflicts (destination exists):")
        for m in conflicts:
            print(f"  {m.src} -> {m.dst}")
    if dry_run:
        return 1 if conflicts else 0
    if not yes and sys.stdin.isatty():
        answer = input("apply? [y/N] ").strip().lower()
        if answer != "y":
            print("aborted")
            return 2
    try:
        log_path = execute_migration(moves, host)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if log_path:
        print(f"log: {log_path}")
    return 0
