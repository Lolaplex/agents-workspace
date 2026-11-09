"""Console entry: ``agents-workspace``."""

from __future__ import annotations

import argparse
import json
import sys

from .workspace import ALL_DOMAINS, Workspace


def _print(obj: object) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def cmd_info(args: argparse.Namespace) -> int:
    ws = Workspace.from_env()
    if args.json:
        _print(ws.to_dict())
        return 0
    print(f"root      {ws.root}")
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


def cmd_init(args: argparse.Namespace) -> int:
    ws = Workspace.open(args.root) if args.root else Workspace.from_env()
    ws = ws.init(label=args.label, default_persona=args.persona)
    _print(ws.to_dict())
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    rows = [ws.to_dict() for ws in Workspace.list_all()]
    if args.json:
        _print(rows)
        return 0
    for row in rows:
        print(f"{row['id'] or '-':<20}{row['label'] or '-':<20}{row['root']}")
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    ws = Workspace.create(args.label)
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
        description="One root for every agents domain.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    info = sub.add_parser("info", help="Show the resolved workspace and every domain path.")
    info.add_argument("--json", action="store_true")
    info.set_defaults(func=cmd_info)

    path = sub.add_parser("path", help="Print one domain path.")
    path.add_argument("domain", choices=sorted(ALL_DOMAINS))
    path.set_defaults(func=cmd_path)

    init = sub.add_parser("init", help="Create the workspace directories and workspace.json.")
    init.add_argument("--root", default="")
    init.add_argument("--label", default="")
    init.add_argument("--persona", default="")
    init.set_defaults(func=cmd_init)

    migrate = sub.add_parser("migrate", help="Move legacy ~/.agents domains into this workspace.")
    migrate.add_argument("--dry-run", action="store_true")
    migrate.set_defaults(func=cmd_migrate)

    list_p = sub.add_parser("list", help="List the default and every registered workspace.")
    list_p.add_argument("--json", action="store_true")
    list_p.set_defaults(func=cmd_list)

    create = sub.add_parser("create", help="Create a new workspace under the workspaces dir.")
    create.add_argument("--label", required=True)
    create.set_defaults(func=cmd_create)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))
