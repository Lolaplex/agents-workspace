"""Workspace member registry: ``<ws>/members.json`` (D-members).

``<host>/users.json`` (agents-keys) binds aliases and DIDs to user ids;
``<ws>/members.json`` is the per-workspace authz list the relay checks.
Sync only merges it — membership is authored here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

from .workspace import Workspace, _valid_id

__all__ = [
    "read_members",
    "write_members",
    "member_role",
    "add_member",
    "remove_member",
    "migrate_members",
]


def _member_id(raw: str) -> str:
    try:
        return _valid_id(raw)
    except ValueError:
        raise ValueError(f"invalid member id: {raw!r}") from None


def _check(row: dict[str, Any]) -> dict[str, Any]:
    uid = _member_id(str(row.get("id") or ""))
    role = str(row.get("role") or "").strip()
    if not role:
        raise ValueError(f"member {uid} has no role")
    out = {"id": uid, "role": role}
    did = str(row.get("did") or "").strip()
    if did:
        out["did"] = did
    return out


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"members": rows}, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def read_members(ws: Workspace) -> list[dict[str, Any]]:
    """Member rows ``[{id, role, did?}]``; empty list when absent/corrupt."""
    path = ws.members
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = data.get("members") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    return [m for m in rows if isinstance(m, dict) and m.get("id")]


def write_members(
    ws: Workspace, members: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Validate and atomically replace the registry. Returns stored rows."""
    rows = [_check(m) for m in members]
    _write(ws.members, rows)
    return rows


def member_role(ws: Workspace, user_id: str) -> str:
    """Role of ``user_id`` in this workspace, or "" when not a member."""
    for m in read_members(ws):
        if m.get("id") == user_id:
            return str(m.get("role") or "")
    return ""


def add_member(
    ws: Workspace, user_id: str, *, role: str = "member", did: str = ""
) -> dict[str, Any]:
    """Upsert one member (re-adding replaces the row)."""
    rows = [m for m in read_members(ws) if m.get("id") != user_id]
    row = _check({"id": user_id, "role": role, "did": did})
    rows.append(row)
    _write(ws.members, rows)
    return row


def remove_member(ws: Workspace, user_id: str) -> bool:
    rows = read_members(ws)
    kept = [m for m in rows if m.get("id") != user_id]
    if len(kept) == len(rows):
        return False
    _write(ws.members, kept)
    return True


def migrate_members(
    ws: Workspace, users_path: Path | str | None = None
) -> list[dict[str, Any]]:
    """Map ``<host>/users.json`` onto ``members.json`` (D-members).

    Existing members keep their role; every mapped user missing from the
    registry is appended — nothing is removed. The first mapped user
    becomes ``owner`` when the registry has none yet, the rest land as
    ``member``. Idempotent.
    """
    path = Path(users_path) if users_path else ws.identity
    users: dict[str, Any] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("users"), dict):
                users = data["users"]
        except (OSError, json.JSONDecodeError):
            pass
    rows = read_members(ws)
    have = {str(m.get("id")) for m in rows}
    has_owner = any(m.get("role") == "owner" for m in rows)
    for uid, rec in users.items():
        uid = str(uid or (rec or {}).get("id") or "")
        if not uid or uid in have:
            continue
        try:
            _member_id(uid)
        except ValueError:
            continue
        role = "member" if has_owner else "owner"
        has_owner = True
        rows.append(
            _check(
                {
                    "id": uid,
                    "role": role,
                    "did": str((rec or {}).get("did") or ""),
                }
            )
        )
        have.add(uid)
    _write(ws.members, rows)
    return rows
