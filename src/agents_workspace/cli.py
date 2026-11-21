"""Console entry: ``agents-workspace``."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import warnings
from pathlib import Path

from .secrets import (
    delete_secret,
    doctor,
    list_secret_names,
    secret_inventory,
    write_secret,
)
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


def cmd_delete(args: argparse.Namespace) -> int:
    host = Host.from_env()
    if not host.workspace_exists(args.id):
        print(f"unknown workspace id: {args.id!r}", file=sys.stderr)
        return 2
    if not args.force:
        if not sys.stdin.isatty():
            print("refusing without --force in non-interactive mode",
                  file=sys.stderr)
            return 2
        typed = input(f"Type {args.id} to confirm: ").strip()
        if typed != args.id:
            print("aborted", file=sys.stderr)
            return 2
    try:
        out = host.delete(args.id, hard=args.hard)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.hard:
        print(f"deleted {args.id}")
    else:
        print(f"archived {args.id} -> {out}")
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
    from .migrate import rollback, run_cli

    host = Host.from_env()
    if args.rollback:
        restored = rollback(Path(args.rollback))
        print(f"restored {restored} entries")
        return 0
    return run_cli(
        host,
        ws_id=args.workspace or "default",
        dry_run=args.dry_run,
        yes=args.yes,
    )


def _resolve_cli_ws(host: Host, ws_id: str) -> Workspace | None:
    if ws_id:
        if not host.workspace_exists(ws_id):
            raise ValueError(f"unknown workspace id: {ws_id!r}")
        return host.workspace(ws_id)
    try:
        return host.select_workspace()
    except ValueError:
        return None


def cmd_secrets(args: argparse.Namespace) -> int:
    host = Host.from_env()
    sub = args.secrets_cmd
    ws_id = getattr(args, "workspace", "") or ""
    if sub == "list":
        try:
            ws = _resolve_cli_ws(host, ws_id)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if args.json:
            _print(secret_inventory(host=host, ws=ws))
            return 0
        for name in list_secret_names(host=host, ws=ws, scope=args.scope):
            print(name)
        return 0
    scope = args.scope
    ws = None
    if scope == "workspace" or ws_id:
        try:
            ws = host.workspace(ws_id) if ws_id and host.workspace_exists(ws_id) else (
                Workspace.from_env() if not ws_id else None
            )
            if ws is None:
                raise ValueError(f"unknown workspace id: {ws_id!r}")
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if sub == "set":
        raw = args.value
        if raw is None:
            if args.stdin or not sys.stdin.isatty():
                raw = sys.stdin.read().rstrip("\r\n")
            else:
                raw = getpass.getpass(f"Value for {args.key}: ")
        try:
            out = write_secret(args.key, raw, host=host, ws=ws, scope=scope)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        _print({"status": "ok", "key": args.key, "scope": scope, "path": str(out)})
        return 0
    if sub == "unset":
        try:
            removed = delete_secret(args.key, host=host, ws=ws, scope=scope)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        _print({"status": "ok" if removed else "missing", "key": args.key, "removed": removed})
        return 0
    return 2


def cmd_doctor(args: argparse.Namespace) -> int:
    report = doctor(Host.from_env())
    if args.json:
        _print(report)
    else:
        print(f"host   {report['host']}")
        print(f"status {report['status']} ({len(report['issues'])} issues)")
        for issue in report["issues"]:
            print(f"[{issue['severity']}] {issue['path']}: {issue['message']}")
    if args.strict and report["issues"]:
        return 1
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

    delete = sub.add_parser(
        "delete",
        help="Archive <host>/workspaces/<id> to workspaces/.trash (or --hard remove).",
    )
    delete.add_argument("id")
    delete.add_argument("--force", action="store_true",
                        help="Skip the type-to-confirm prompt.")
    delete.add_argument("--hard", action="store_true",
                        help="Permanently delete instead of archiving.")
    delete.set_defaults(func=cmd_delete)

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

    migrate = sub.add_parser("migrate", help="Migrate a legacy ~/.agents layout to Host/Workspace.")
    migrate.add_argument("--dry-run", action="store_true")
    migrate.add_argument("--yes", action="store_true", help="Apply without asking.")
    migrate.add_argument("--workspace", default="default",
                         help="Workspace id receiving legacy data (default: default).")
    migrate.add_argument("--rollback", default="", metavar="LOG",
                         help="Reverse a previous run using its JSON log.")
    migrate.set_defaults(func=cmd_migrate)

    sec = sub.add_parser("secrets", help="Manage host-only secrets vault.")
    sec_sub = sec.add_subparsers(dest="secrets_cmd", required=True)

    sec_set = sec_sub.add_parser("set", help="Store a secret in host or ws vault.")
    sec_set.add_argument("key")
    sec_set.add_argument("--scope", choices=("host", "workspace"), default="host")
    sec_set.add_argument("--workspace", default="")
    sec_set.add_argument("--value", default=None)
    sec_set.add_argument("--stdin", action="store_true")
    sec_set.set_defaults(func=cmd_secrets)

    sec_unset = sec_sub.add_parser("unset", help="Remove a secret from vault.")
    sec_unset.add_argument("key")
    sec_unset.add_argument("--scope", choices=("host", "workspace"), default="host")
    sec_unset.add_argument("--workspace", default="")
    sec_unset.set_defaults(func=cmd_secrets)

    sec_list = sec_sub.add_parser("list", help="List secret key names (never values).")
    sec_list.add_argument("--scope", choices=("all", "host", "workspace"), default="all")
    sec_list.add_argument("--workspace", default="")
    sec_list.add_argument("--json", action="store_true")
    sec_list.set_defaults(func=cmd_secrets)

    doc = sub.add_parser("doctor", help="Audit secret hygiene, permissions, and legacy state.")
    doc.add_argument("--json", action="store_true")
    doc.add_argument("--strict", action="store_true")
    doc.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))
