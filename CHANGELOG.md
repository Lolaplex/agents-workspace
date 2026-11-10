# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `Host` type: `AGENTS_HOST_DIR` (default `~/.agents`) holds infra only
  (`keys/`, `secrets.json`, `run/`, `cache/`, `logs/`, `clients/`,
  `agents.json`, `mcp.json`, `users.json`, `browser/`, `host_paths.json`).
- `Host.workspace(id)`, `Host.list_workspaces()`, `Host.create(id, label,
  persona)`, `Host.select_workspace()` — workspaces live at
  `<host>/workspaces/<id>`; first start creates `default`.
- `host_dir()`, `default_host_dir()` helpers.
- New workspace paths: `members.json`, `sync_config.json`, `mcp.json`,
  `calendar.json`, `content/`, `work/`, `browser/` (artifacts).
- Reserved `parent_workspace` field (unused, for later job workspaces).

### Added

- CLI `create <id>`, `show [<id>]`, `env [<id>]` subcommands; `docs/NAMING.md`
  holds the canonical naming/hierarchy spec.

### Changed

- **BREAKING**: `AGENTS_WORKSPACE=<id>` now selects a workspace by id; the old
  `AGENTS_WORKSPACE_ROOT` path model is a deprecated ad-hoc bridge.
- Domain env overrides (`AGENTS_MEMORY_PATH` & co.) are test/debug only: they
  apply only to the workspace chosen at CLI entry and emit a `UserWarning`;
  `Host.workspace()`, `list_workspaces()` and `open()` never read them.
- `Workspace.env()` rewritten: sets `AGENTS_HOST_DIR`, `AGENTS_WORKSPACE`,
  `AGENTS_WORKDIR`, optional `AGENTS_PERSONA`, `AGENTS_KEYS_DIR` and every
  domain dir (new names plus one-release aliases); strips all removed keys
  so parent values can never leak. New `Workspace.env_keys()` lists the
  resolver-owned vars for stripping/comparing.
- CLI reworked around the Host: `create <id>` replaces `init --root`
  (deprecated alias, refuses `--root` outside `<host>/workspaces`), `list`
  reads the host, `show`/`env` resolve by id or env selection.
- README rewritten for the Host > Workspace model; `AGENTS_HOME` documented
  only as a one-release deprecated alias.
- `Workspace` now carries `host: Host` and `dir` (was `root`).
- `identity` resolves to `<host>/users.json` (`AGENTS_USERS_FILE`).

### Deprecated

- `AGENTS_HOME` -> `AGENTS_HOST_DIR` (one release).
- `AGENTS_WORKSPACE_DIR` -> `AGENTS_WORKDIR` (one release).
- `AGENTS_IDENTITY_PATH` -> `AGENTS_USERS_FILE` (one release).
- `AGENTS_WORKSPACE_ROOT` -> ad-hoc workspace dir bridge (one release).
- `AGENTS_WORKSPACES_DIR` -> `<host>/workspaces` (one release).
- `Workspace.root` -> `Workspace.dir`, `Workspace.open(dir)`,
  `Workspace.create(label)`, `Workspace.explicit_root` (one release).

### Removed

- `legacy_root()` and `workspaces_dir()` module functions (use `host_dir()`
  and `Host.workspaces_dir`).

## [0.1.0] - 2026-11-10

- Initial release.
