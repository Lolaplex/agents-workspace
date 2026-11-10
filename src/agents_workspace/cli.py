"""Console entry: ``agents-workspace``."""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

from .workspace import ALL_DOMAINS, Host, Workspace


def _print(obj: object) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def _select(args: argparse.Namespace) -> Workspace:
    ws_id = getattr(args, "id", "") or ""
    if not ws_id:
        return Workspace.from_env()
    host = Host.from_env()
    if not host.workspace_exists(ws_id):
        raise ValueError(f"unknown workspace id: {ws_id!r}")
    return host.workspace(ws_id)


def cmd_info(args: argparse.Namespace) -> int:
    ws = Workspace.from_env()
    if args.json:
        _print(ws.to_dict())
        return 0
    print(f"host      {ws.host.dir}")
    print(f"dir       {ws.dir}")
    print(f"workdir   {ws.workdir}")
    print(f"id        {ws.id or '-'}")
    print(f"label     {ws.label or '-'}")
    print(f"persona   {ws.default_persona}")
    for name in ALL_DOMAINS:
        print(f"{name:<10}{ws.domain(name)}")
    return 0


def cmd_path(args: argparse.Namespace) -> int:
    ws = Workspace.from_env()
    try:
        print(ws.domain(args.domain))
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    ws = Host.from_env().create(
        args.id, label=args.label, persona=args.persona or "default"
    )
    _print(ws.to_dict())
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    rows = [ws.to_dict() for ws in Host.from_env().list_workspaces()]
    if args.json:
        _print(rows)
        return 0
    for row in rows:
        print(f"{row['id'] or '-':<20}{row['label'] or '-':<20}{row['dir']}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    try:
        ws = _select(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        _print(ws.to_dict())
        return 0
    print(f"host      {ws.host.dir}")
    print(f"dir       {ws.dir}")
    print(f"workdir   {ws.workdir}")
    print(f"id        {ws.id or '-'}")
    print(f"label     {ws.label or '-'}")
    print(f"persona   {ws.default_persona}")
    return 0


def cmd_env(args: argparse.Namespace) -> int:
    try:
        ws = _select(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    child = ws.env({})
    for key in sorted(child):
        print(f"{key}={child[key]}")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    warnings.warn(
        "agents-workspace init is deprecated; use 'create <id>' "
        "(alias removed after one release)",
        DeprecationWarning,
    )
    host = Host.from_env()
    if args.root:
        root = Path(args.root).expanduser().resolve()
        try:
            root.relative_to(host.workspaces_dir)
        except ValueError:
            print(
                f"init --root must be inside {host.workspaces_dir}; "
                "use 'create <id>' instead",
                file=sys.stderr,
            )
            return 2
        ws = host.workspace(root.name)
    else:
        ws = Workspace.from_env()
    ws = ws.init(label=args.label, default_persona=args.persona)
    _print(ws.to_dict())
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    ws = Workspace.from_env()
    moves = ws.migrate(dry_run=args.dry_run)
    if not moves:
        print("nothing to migrate")
        return 0
    verb = "would move" if args.dry_run else "moved"
    for src, dst in moves:
        print(f"{verb} {src} -> {dst}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agents-workspace",
        description="Host > Workspace > Persona > Session path resolver.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    info = sub.add_parser("info", help="Show the resolved workspace and every domain path.")
    info.add_argument("--json", action="store_true")
    info.set_defaults(func=cmd_info)

    path = sub.add_parser("path", help="Print one domain path.")
    path.add_argument("domain", choices=sorted(ALL_DOMAINS))
    path.set_defaults(func=cmd_path)

    create = sub.add_parser("create", help="Create <host>/workspaces/<id> plus its first persona.")
    create.add_argument("id")
    create.add_argument("--label", default="")
    create.add_argument("--persona", default="")
    create.set_defaults(func=cmd_create)

    list_p = sub.add_parser("list", help="List every workspace on the host.")
    list_p.add_argument("--json", action="store_true")
    list_p.set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="Show one workspace (default: env-selected).")
    show.add_argument("id", nargs="?", default="")
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=cmd_show)

    env_p = sub.add_parser("env", help="Print resolver-owned KEY=VALUE lines for a workspace.")
    env_p.add_argument("id", nargs="?", default="")
    env_p.set_defaults(func=cmd_env)

    init = sub.add_parser(
        "init", help="Deprecated alias for create (one release)."
    )
    init.add_argument("--root", default="")
    init.add_argument("--label", default="")
    init.add_argument("--persona", default="")
    init.set_defaults(func=cmd_init)

    migrate = sub.add_parser("migrate", help="Move legacy host-level domains into a workspace.")
    migrate.add_argument("--dry-run", action="store_true")
    migrate.set_defaults(func=cmd_migrate)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))
