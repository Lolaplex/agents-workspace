# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **Fail-closed sync policy (#44)**: `sync_policy` defaults to `"local"` on `Host.create`/`Workspace.init`/missing meta (invalid values warn and fail closed to `"local"`). `sync_hosts == ()` is now CLOSED — the open-herd wildcard is gone; only explicit hub device ids pass `ws.allows_device(device_id)` (`ws.is_syncable()` for the policy check). Malformed `sync_hosts`/`sync_policy`/`default_host`/`repos`/`execution` in `workspace.json` never crash `host.workspace()` — they fail closed and set `ws.policy_error`. Writes enforce the invariant `local` + non-empty `sync_hosts` -> `ValueError` (`Host.create`, `update_workspace`, `init`). `Host.create()` now accepts `sync_policy`/`sync_hosts`/`default_host`/`repos`/`execution`; `Workspace.init()`, `_ad_hoc_open()` and `to_dict()` preserve all five fields.
- **Strict ownership validation**: `execution` accepts only `owner`/`failover`/`lease_sec` (secret-looking keys rejected, `lease_sec` a real int in [15,3600]); `default_host` is a strict host-id regex with no coercion; `repos[]` requires a strict slug (`org/app`, no `..`, no duplicates) and refuses host-local `path` keys — checkout paths live in the new `<host>/host_paths.json` API (`read_host_paths()`/`set_host_repo_path()`/`resolve_repo_path()`), and `host.host_paths` joins `denied_paths()`.
- **Host identity isolation**: `_agents_keys_did` now pins `AGENTS_HOST_DIR`/`AGENTS_KEYS_DIR` to the owning host so isolated `Host(tmp)` instances can never leak the real `~/.agents` DID; new `Host.sync_node_id()` returns an HLC-safe node id (`k_<sha256[:16]>` for `:`-carrying DIDs).
- **Secret boundary**: `resolve_secret(..., scope="workspace")` searches ONLY `<ws>/secrets.json` — never the host vault, OS keyring, `legacy <host>/secrets.json`, or env — and never resolves `INFRA_SECRET_KEYS`. `expand_secrets(..., scope=...)` forwards the scope and blocks infra keys for workspace manifests. `doctor()` now flags malformed `sync_policy`/`sync_hosts` and secret-like keys in `<ws>/workspace.json`/`<ws>/config.json`. `AGENTS_WORKDIR_LOCK`/`AGENTS_WORKDIR_ALLOW`/`AGENTS_WORKSPACE_ALLOW` are resolver-owned (`_ENV_KEYS`, no cross-workspace leaks); `AGENTS_STORE_DB` is allowlisted into container envs (`_SAFE_AGENTS_ENV`).

### Added
- `Host.host_id()` (persisted `<host>/host.json`), `run/serve.json` contract (`read_serve_state`/`write_serve_state` with `host_id`), `resolve_entry_workspace()` deterministic workspace pick, `resolve_relay_token()` vault-aware token resolution (#37).
- Per-workspace `workdir`/`workdir_lock`/`browser_profile` on `update_workspace()` plus `<ws>/config.json` `approval.mode` (off|ask|strict); `AGENTS_WORKDIR`/`AGENTS_WORKDIR_LOCK` env contract (#38).
- `ws.store_db`/`ws.store_exports` + `AGENTS_STORE_DB` env ownership for workspace-scoped storage (#39).

### Added
- Migrate now merges instead of aborting: existing destination dirs merge leaf-by-leaf (new `merge`/`conflict` log kinds), byte-identical duplicates are dropped, and still-colliding entries are quarantined to `<host>/conflicts/<stamp>/` (fully rollback-able via the run log). Half-migrated hosts — where a workspace already holds domain dirs — no longer block `agents-workspace migrate`.
- Migrate sweeps legacy `~/.klanker` instance payloads: `instances/<id>/workspaces/<ws>` merge into `<host>/workspaces/<ws>`, dead `daemon.pid` files are deleted, and the remaining payload is archived under `<host>/archive/klanker/<id>/`. The live registry (`instance.json`, `relay-secret`, `settings.json`) stays untouched.
- Host keep-list additions: `providers/` (harness/relay manifests), `voice.json` (host-level TTS config), `AGENTS.md`/`CLAUDE.md` (root inject targets, C62 — previously wrongly moved into the workspace), `archive/` and `conflicts/` (migration output dirs). `voice_inbox/` remaps to `<host>/run/voice_inbox/`, `*.bak-*` entries archive to `<host>/archive/`, a stale empty `workspace/` dir is deleted, and a stale `klanker_prompt.txt` is dropped when the run copy already exists.
- `delegate_env()`/`delegate_depth()` (exported): `AGENTS_DELEGATE_DEPTH` counter incremented in the parent before a delegate turn exists, refusing past `AGENTS_DELEGATE_MAX_DEPTH` (default 8). Advisory env-hint per PERIMETER.md — the doc now states the rule plainly: delegates share the parent privilege class, the hard boundary is the deploy container, and a real in-turn overlay waits on the ExecBackend interface (agents-runtime#2 / harness#8).
- `agents_workspace.inbox` messaging schema (#17): tasks carry `from_persona`/`to_persona`, `thread_id` (`thr_…`, minted on create, kept across hops), `hop`/`max_hops` (default 0/3), `mode` (`ask`|`handoff`), `wake`, `status` (`open`|`running`|`done`|`failed`), `result`/`reply_summary`. `handoff` enforces the hop limit and the target's peer policy; `reply_task` writes result fields and returns the task to `from_persona` (hop-counted). `persona_meta()` reads `persona.json` — `accepts_messages: false` refuses all senders, `allow_peers` restricts who may address the persona. Task writes are tmp+`os.replace` atomic everywhere.
- `agents_workspace.members`: `<ws>/members.json` registry (D-members) — `read_members`/`write_members` (`{members: [{id, role, did?}]}`, id-validated, atomic replace), `add_member`/`remove_member`, `member_role` for authz lookups, and `migrate_members` mapping `<host>/users.json` onto the member list (first mapped user becomes `owner` when none exists, the rest `member`; idempotent, removes nothing). Relay authz consumes `member_role`; the file is authored here, sync only merges.

### Fixed
- `inbox.handoff()` is now crash-atomic: the `handoff` event is persisted at the source inbox via tmp+`os.replace`, then the file moves with a single `os.replace` rename. A crash mid-handoff can no longer leave the task in two inboxes (duplicate owners) — at every point it lives in exactly one.
- Inbox task files survive transient Windows locks: `_write_task` now uses a unique tmp name per call (two writers can no longer collide on the shared `<id>.tmp`), and both the replace step (`_replace`, covering `_relocate`) and `_load` retry short PermissionError/OSError windows. A reader holding a task file open without `FILE_SHARE_DELETE` (any `open()` handle), an AV scan, or an indexer could otherwise turn a concurrent `os.replace` into PermissionError or a mid-replace open into a read failure — the cause of the flaky `handoff?wake=1` test failures (RemoteDisconnected / "never reached done") seen on Windows. POSIX behaviour is unchanged.

### Removed
- `AGENTS_HOME` env var: the deprecated `AGENTS_HOST_DIR` alias is no longer read by `host_dir()` or `Workspace.from_env()`, and the old "home points at a workspace root" ad-hoc fallback is gone. Set `AGENTS_HOST_DIR`; `Workspace.env()` still strips inherited `AGENTS_HOME` values so children never see the dead name.

### Fixed
- Runtime and migration subprocesses decode child output as UTF-8 (`errors="replace"`) instead of the locale default (`cp1252` on Western-European Windows). Non-ASCII tool output and child logs no longer corrupt.

### Added
- `allowed_roots(ws, env=None)`: canonical list of dirs the workspace lock must not treat as escapes (ws tree, host infra, env-listed dirs, `AGENTS_WORKDIR_ALLOW`). Single source for `agents_terminal.policy`, `runner.executor`, and `runner.delegate`; callers add the workdir root themselves.
- `resolve_secret(key, ws=None, env=None)`: secret lookup ordered `<ws>/secrets.json` → `<host>/secrets.json` → env (flat `{"KEY": "value"}` files, D-secrets). Lets a workspace pin per-ws keys without exporting them.
- `docs/PERIMETER.md`: layer boundary table (HOST > WORKSPACE > PERSONA > SESSION) marking which boundary is kernel (openshell/container), env-hint (workdir lock, strippable via `env -u` — anti-accident only), or parent-enforced (approval, allowlist, env construction, secret precedence).
- `Host.delete(id)` and `agents-workspace delete <id>`: archives the workspace to `workspaces/.trash/<id>-<utc>` (hidden from `list`, recoverable) or removes it entirely with `--hard`. Interactive runs ask for a type-to-confirm; `--force` skips the prompt (required non-interactively). Deleting the env-selected workspace (`AGENTS_WORKSPACE`) is refused.

### Changed
- `.mailmap` maps legacy `Klix`/`Felix Kempf` author names to `Klix927` (no history rewrite; display only).

### Fixed
- Domain-override warning no longer fires when an `AGENTS_*_DIR`/users-file value equals the canonical resolved path (e.g. a serve process pinning its entry workspace env for children). Real divergent overrides still warn.

### Added
- `docs/PERSONAS.md`: P2 persona rules home — equal personas (no
  hierarchy), first persona `default` per workspace, role as
  description text, inbox + explicit task ownership for handoffs,
  session key `ws+user+persona`, API surface. Extends `docs/NAMING.md`
  without contradicting the closed P1 spec.
- `agents_workspace.inbox`: per-persona task inbox + explicit handoff
  ownership. A task is one JSON file in exactly one persona's
  `<ws>/personas/<p>/inbox/`; `handoff()` moves the file to the target
  persona (owner = location, no hierarchy, no default-persona
  privilege) and appends a `handoff` event. `create_task`,
  `list_inbox`, `find_task`, `task_owner` included; ids validated.
- `agents-workspace migrate`: explicit one-shot host migration from a legacy
  `~/.agents` layout to `Host/Workspace`. Pure planner, `--dry-run`, `--yes`,
  `--rollback <log>`; lock at `<host>/run/migrate.lock`, JSON op-log under
  `<host>/logs/migrate-<ts>.json`, conflict preflight aborts before any move,
  idempotent (`already migrated`), byte-identical rollback. Moves workspace
  domains and files into `workspaces/default`, identity.json ->
  `users.json`, known-dids -> `keys/`, `~/.agents-relay` ->
  `<ws>/relay`, `~/.client` -> `<host>/clients`, `~/.agents-workspaces/*`
  -> `workspaces/*`, deletes dead `~/.klanker/daemon.pid`, catch-all for
  unknown entries.
- Real-host hardening after dry-run on a live `~/.agents`: legacy
  `browser*`/`chromium*` Chromium profile dirs stay host-level (the
  `<ws>/browser` domain is for artifacts, not profiles); `identity.json`
  union-merges into an existing `users.json` (byte-exact rollback of both
  files); `harness-content` merges into `<ws>/content`; `repl` ->
  `<host>/clients`, `mcp-tools-cache.json` -> `<host>/cache` (stale copy
  deleted when a fresher one exists), `klanker_prompt.txt` ->
  `<host>/run/klanker_prompt_<ws>.txt`.
- `Host.has_legacy_layout()` public check used by local serves to refuse
  unmigrated roots.

### Added
- `agents_workspace.compat.read_alias(env, new, old)`: shared deprecated-alias reader (new wins, old warns, one release).
- `DOMAIN_ENV_ALIASES`: `AGENTS_MEMORY_PATH` -> `AGENTS_MEMORY_DIR`, `AGENTS_RELAY_STATE` -> `AGENTS_RELAY_DIR` (deprecated aliases, one release). `AGENTS_RULES_DIR` already canonical.

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
