"""Persona inbox and explicit task ownership (P2 handoffs).

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
]


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _mint_id() -> str:
    import secrets

    return "tsk_" + secrets.token_hex(8)


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
) -> dict[str, Any]:
    """Create a task owned by ``persona`` and return it."""
    tid = _valid_id(task_id) if task_id else _mint_id()
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
        "events": [{"at": _ts(), "event": "create", "owner": persona, "note": note}],
    }
    _save(path, task)
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
    src = _task_path(persona_inbox(ws, owner), task_id)
    dst = _task_path(persona_inbox(ws, target), task_id)
    task.setdefault("events", []).append(
        {"at": _ts(), "event": "handoff", "from": owner, "to": target, "note": note}
    )
    _save(dst, task)
    src.unlink()
    task["owner"] = target
    return task
