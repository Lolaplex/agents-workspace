# AGENTS.md — agents-workspace Developer Guidelines

## Architecture Principles

- **Host > Workspace > Persona > Session resolver.** The Host
  (`AGENTS_HOST_DIR`, default `~/.agents`) holds infrastructure only:
  `keys/`, `secrets/`, `run/`, `cache/`, `logs/`, `clients/`,
  `providers/`, `mcp/`, `agents.json`, `users.json`, `host_paths.json`.
  Every data workspace lives at `<host>/workspaces/<id>` and is
  selected by id (`AGENTS_WORKSPACE`), never by a process-global root.
- **Pure Python standard library.** Stdlib first, zero heavy
  dependencies. No daemons, no network, optional lazy keyring.
- **Precedence**: explicit env, then workspace default, then global
  fallback. Domain overrides are test/debug only.
- **Dir domains** (inside `<ws>/`):
  - `memory`: `AGENTS_MEMORY_DIR` -> `<ws>/memory`
  - `rules`: `AGENTS_RULES_DIR` -> `<ws>/rules`
  - `skills`: `AGENTS_SKILLS_DIR` -> `<ws>/skills`
  - `modules`: `AGENTS_MODULES_DIR` -> `<ws>/modules`
  - `schedules`: `AGENTS_SCHEDULES_DIR` -> `<ws>/schedules`
  - `traces`: `AGENTS_TRACES_DIR` -> `<ws>/traces`
  - `routine-runs`: `AGENTS_ROUTINE_RUNS_DIR` -> `<ws>/routine-runs`
  - `relay`: `AGENTS_RELAY_DIR` -> `<ws>/relay`
  - `personas`: `AGENTS_PERSONAS_DIR` -> `<ws>/personas`
  - `content`: `<ws>/content` (paged tool bodies)
  - `work`: `<ws>/work` (session working dirs)
  - `browser`: `<ws>/browser`
  - `claude`: `~/.claude` (home dir, not inside `<ws>`)
- **File domains**: `config` -> `<ws>/config.json`, `members` ->
  `<ws>/members.json`, `sync_config` -> `<ws>/sync_config.json`,
  `mcp` -> `<ws>/mcp.json`, `calendar` -> `<ws>/calendar.json`,
  `workspace` -> `<ws>/workspace.json`, `store` -> `<ws>/store.db`
  (path contract only — raw `.db` files never sync), `secrets` ->
  host vault `<host>/secrets/{host,ws-<id>}.json` (0700/0600),
  `identity` -> `<host>/users.json` (`AGENTS_USERS_FILE`).

## `workspace.json` ownership fields

Written by `Host.create` / `Host.update_workspace`
(`workspace.py`); reads are fail-closed:

- `sync_policy`: `local | synced | shared`. Missing, empty, or
  malformed values resolve to `local` and never emit ops (`local`
  plus non-empty `sync_hosts` is a `ValueError`).
- `sync_hosts`: hub device ids (channel ids, not `Host.host_id()`);
  the hub fan-out allowlist. Strict write-side validation — a bad
  shape fails closed to `()`.
- `default_host`: host-picker id used when a session is unbound.
- `repos`: `[{slug, url, checkout_mode}]` with
  `checkout_mode: shared | worktree`; paths stay host-local.
- `execution`: `{owner, failover: standby | none, lease_sec}` —
  `owner` is the peer id that owns execution (or `""`),
  `lease_sec` an int in `[15, 3600]`; reads degrade to
  `{failover: "none", lease_sec: 45}` when unparsable.
- Plus `label`, `default_persona`, `isolation`, `image`,
  `parent_workspace`.

## Secret boundary

`resolve_secret` in `secrets.py` has three resolution paths:

- **Workspace/provider secrets** (default `scope="auto"`):
  `ws-<id>.json` > legacy `<ws>/secrets.json` > keyring
  (ws service, then host service) > `host.json` > legacy
  `<host>/secrets.json` > `ENV` > `/run/secrets`.
  `AGENTS_SECRETS_ORDER=env-first` flips `ENV` + `/run/secrets`
  to the front.
- **Host/infra secrets** (`scope="host"` or any
  `INFRA_SECRET_KEYS` key): `ENV` > `/run/secrets` > keyring >
  `host.json` > legacy `<host>/secrets.json` — never reads
  workspace files regardless of `AGENTS_SECRETS_ORDER`.
- **`scope="workspace"`** is a hard boundary: only the target
  workspace's own vault `ws-<id>.json`, then legacy
  `<ws>/secrets.json` — never `host.json`, another workspace's
  file, the keyring, `/run/secrets`, or `ENV`.

`INFRA_SECRET_KEYS` (`RELAY_SECRET`, `GATEWAY_SECRET`,
`RELAY_WEB_TOKEN`, `TELEGRAM_BOT_TOKEN`, …) are stripped from every
env the resolver hands to a turn — host infra never reaches agent
turns, jails, or containers. `users.json`, `host_paths.json`,
provider manifests and pairing secrets are host-only and never sync.

## Commands

- Run test suite: `python -m pytest` or `python tests/run_all_tests.py`
- Resolved workspace + domain paths: `agents-workspace info`
- `agents-workspace create|list|show|delete|path|env|init`
- Legacy `~/.agents` -> Host/Workspace: `agents-workspace migrate [--dry-run|--yes|--rollback LOG]`
- Secret vault: `agents-workspace secrets set|unset|list`
- Hygiene audit: `agents-workspace doctor`; aged-entry prune: `agents-workspace gc`
