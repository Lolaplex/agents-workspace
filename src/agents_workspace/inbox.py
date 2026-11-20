"""Persona inbox, task ownership and persona messaging (P2, #14/#17).

A task is a single JSON file inside exactly one persona's inbox:

    <ws>/personas/<persona>/inbox/<task_id>.json

Ownership is therefore singular by construction: the persona whose
``inbox/`` directory contains the file owns the task. A handoff moves
the file to the target persona's inbox and appends a ``handoff`` event
to the task's event log. There is no implicit default-persona
privilege and no hierarchy — a role is persona description text only.

Filesystem only; no network, no daemons.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .workspace import _ID_OK, _valid_id

if TYPE_CHECKING:  # pragma: no cover
    from .workspace import Workspace

__all__ = [
    "persona_inbox",
    "list_inbox",
    "create_task",
    "find_task",
    "task_owner",
    "handoff",
    "reply_task",
    "persona_meta",
]

_MODES = ("ask", "handoff")
_STATUS = ("open", "running", "done", "failed")
DEFAULT_MAX_HOPS = 3


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _mint_id() -> str:
    import secrets

    return "tsk_" + secrets.token_hex(8)


def _mint_thread() -> str:
    import secrets

    return "thr_" + secrets.token_hex(8)


def _task_path(inbox: Path, task_id: str) -> Path:
    return inbox / (_valid_id(task_id) + ".json")


def persona_inbox(ws: "Workspace", persona: str) -> Path:
    """Return (and create) ``<ws>/personas/<persona>/inbox``."""
    pid = _valid_id(persona)
    inbox = ws.personas / pid / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    return inbox


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, task: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(task, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _write_task(path: Path, task: dict[str, Any]) -> None:
    """Atomic task write: tmp file + replace, never a torn JSON body."""
    tmp = path.with_suffix(".tmp")
    _save(tmp, task)
    os.replace(tmp, path)


def _relocate(ws: "Workspace", task: dict[str, Any], task_id: str,
              owner: str, target: str) -> None:
    """Crash-atomic single-owner move between inboxes (#14)."""
    src = _task_path(persona_inbox(ws, owner), task_id)
    dst = _task_path(persona_inbox(ws, target), task_id)
    _write_task(src, task)
    os.replace(src, dst)


def persona_meta(ws: "Workspace", persona: str) -> dict[str, Any]:
    """``<ws>/personas/<id>/persona.json`` as dict ({} when absent/corrupt)."""
    path = ws.personas / _valid_id(persona) / "persona.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _check_peer(ws: "Workspace", from_persona: str, to_persona: str) -> None:
    """Peer policy of the target persona (#17).

    ``accepts_messages: false`` refuses all senders; ``allow_peers`` (when
    a list) restricts who may address the persona. Unset = all workspace
    personas."""
    meta = persona_meta(ws, to_persona)
    if meta.get("accepts_messages") is False:
        raise ValueError(f"persona {to_persona} does not accept messages")
    peers = meta.get("allow_peers")
    if isinstance(peers, list) and from_persona not in [str(p) for p in peers]:
        raise ValueError(f"persona {to_persona} does not accept {from_persona}")


def _hop_window(task: dict[str, Any]) -> tuple[int, int]:
    """(hop, max_hops) with defaults; refuses a task at its hop limit."""
    hop = int(task.get("hop") or 0)
    max_hops = int(task.get("max_hops") or DEFAULT_MAX_HOPS)
    if hop >= max_hops:
        raise ValueError(f"task {task.get('id')} at hop limit ({hop}/{max_hops})")
    return hop, max_hops


def list_inbox(ws: "Workspace", persona: str) -> list[dict[str, Any]]:
    """List tasks currently owned by ``persona`` (inbox files)."""
    inbox = persona_inbox(ws, persona)
    rows: list[dict[str, Any]] = []
    for path in sorted(inbox.glob("*.json")):
        try:
            task = _load(path)
        except (OSError, ValueError):
            continue
        task["owner"] = persona
        rows.append(task)
    rows.sort(key=lambda t: t.get("created_at", ""))
    return rows


def create_task(
    ws: "Workspace",
    persona: str,
    title: str,
    *,
    note: str = "",
    user: str = "",
    task_id: str = "",
    from_persona: str = "",
    to_persona: str = "",
    thread_id: str = "",
    mode: str = "handoff",
    wake: bool = False,
    hop: int = 0,
    max_hops: int = DEFAULT_MAX_HOPS,
    status: str = "open",
    result: str = "",
    reply_summary: str = "",
) -> dict[str, Any]:
    """Create a task owned by ``persona`` and return it.

    ``from_persona`` marks a message from another persona — the target's
    peer policy (``accepts_messages``/``allow_peers`` in persona.json)
    is enforced. ``mode`` is ``ask`` (sync Q&A) or ``handoff`` (async
    transfer); ``thread_id`` mints when absent so replies and sub-
    handoffs correlate back to the caller.
    """
    tid = _valid_id(task_id) if task_id else _mint_id()
    owner = _valid_id(persona)
    if mode not in _MODES:
        raise ValueError(f"bad task mode: {mode!r}")
    if status not in _STATUS:
        raise ValueError(f"bad task status: {status!r}")
    hop, max_hops = int(hop), int(max_hops)
    if hop < 0 or max_hops < 1 or hop > max_hops:
        raise ValueError(f"bad hop window: {hop}/{max_hops}")
    src = _valid_id(from_persona) if from_persona else ""
    dst = _valid_id(to_persona) if to_persona else ""
    if src and src != owner:
        _check_peer(ws, src, owner)
    inbox = persona_inbox(ws, persona)
    path = _task_path(inbox, tid)
    if path.exists():
        raise ValueError(f"task already exists: {tid}")
    task: dict[str, Any] = {
        "id": tid,
        "title": title.strip(),
        "note": note.strip(),
        "user": user.strip(),
        "created_at": _ts(),
        "from_persona": src,
        "to_persona": dst,
        "thread_id": _valid_id(thread_id) if thread_id else _mint_thread(),
        "hop": hop,
        "max_hops": max_hops,
        "mode": mode,
        "wake": bool(wake),
        "status": status,
        "result": result,
        "reply_summary": reply_summary,
        "events": [{"at": _ts(), "event": "create", "owner": persona, "note": note}],
    }
    _write_task(path, task)
    task["owner"] = persona
    return task


def task_owner(ws: "Workspace", task_id: str) -> str:
    """Return the persona owning ``task_id`` or "" when not found."""
    hit = find_task(ws, task_id)
    return hit[0] if hit else ""


def find_task(ws: "Workspace", task_id: str) -> tuple[str, dict[str, Any]] | None:
    """Locate ``task_id`` across all persona inboxes -> (persona, task)."""
    tid = _valid_id(task_id)
    if not ws.personas.is_dir():
        return None
    for pdir in sorted(ws.personas.iterdir()):
        if not pdir.is_dir() or not _ID_OK.match(pdir.name):
            continue
        path = pdir / "inbox" / (tid + ".json")
        if path.is_file():
            task = _load(path)
            task["owner"] = pdir.name
            return pdir.name, task
    return None


def handoff(
    ws: "Workspace",
    task_id: str,
    to_persona: str,
    *,
    from_persona: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Move ownership of ``task_id`` to ``to_persona``.

    ``from_persona`` is optional; when given it must match the current
    owner (explicit routing, no silent steals). The task file moves to
    the target inbox and a ``handoff`` event is appended.
    """
    hit = find_task(ws, task_id)
    if hit is None:
        raise ValueError(f"unknown task: {task_id}")
    owner, task = hit
    target = _valid_id(to_persona)
    if from_persona and from_persona != owner:
        raise ValueError(
            f"task {task_id} is owned by {owner}, not {from_persona}"
        )
    if target == owner:
        return task
    hop, max_hops = _hop_window(task)
    _check_peer(ws, owner, target)
    task["from_persona"] = owner
    task["to_persona"] = target
    task["hop"] = hop + 1
    task["max_hops"] = max_hops
    task["thread_id"] = task.get("thread_id") or _mint_thread()
    task.setdefault("events", []).append(
        {"at": _ts(), "event": "handoff", "from": owner, "to": target, "note": note}
    )
    _relocate(ws, task, task_id, owner, target)
    task["owner"] = target
    return task


def reply_task(
    ws: "Workspace",
    task_id: str,
    *,
    result: str = "",
    reply_summary: str = "",
    status: str = "done",
    note: str = "",
) -> dict[str, Any]:
    """Answer a task: write result fields and hand it back to the sender.

    With ``from_persona`` set the task returns to that persona's inbox —
    the return leg counts a hop and honors peer policy like a handoff.
    Without a sender the fields update in place.
    """
    if status not in _STATUS:
        raise ValueError(f"bad task status: {status!r}")
    hit = find_task(ws, task_id)
    if hit is None:
        raise ValueError(f"unknown task: {task_id}")
    owner, task = hit
    task["result"] = result
    task["reply_summary"] = reply_summary
    task["status"] = status
    target = str(task.get("from_persona") or "")
    if not target or target == owner:
        task.setdefault("events", []).append(
            {"at": _ts(), "event": "reply", "status": status, "note": note}
        )
        _write_task(_task_path(persona_inbox(ws, owner), task_id), task)
        task["owner"] = owner
        return task
    hop, max_hops = _hop_window(task)
    _check_peer(ws, owner, target)
    task["from_persona"] = owner
    task["to_persona"] = target
    task["hop"] = hop + 1
    task["max_hops"] = max_hops
    task.setdefault("events", []).append(
        {"at": _ts(), "event": "reply", "from": owner, "to": target,
         "status": status, "note": note}
    )
    _relocate(ws, task, task_id, owner, target)
    task["owner"] = target
    return task
