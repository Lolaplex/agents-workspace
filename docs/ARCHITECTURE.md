# Architecture map — agents-workspace decision surface (audit 21.11.2026)

What `agents-workspace` decides today, what it could decide, and the gaps
found while auditing all suite repos against the live host.

## 1. What the library owns today

### 1.1 Path contract (the core product)

```
Host    AGENTS_HOST_DIR, default ~/.agents, container /data/.agents
Ws      <host>/workspaces/<id>      AGENTS_WORKSPACE=<id>  (id, never a path)
Persona <ws>/personas/<id>          AGENTS_PERSONA=<id>
Session <ws>/personas/<p>/sessions/<ses>   API/CLI only
workdir any directory               AGENTS_WORKDIR         (attribute, not a level)
```

Host infra (all resolved, not user-configurable):
`keys/  secrets/{host,ws-<id>}.json  run/{serve.json,approvals/,...}  cache/  logs/  clients/{desktop,telegram.json,remotes.json}  providers/
mcp.json  mcp/ws-<id>.json  approval-allowlist.json  users.json  agents.json  voice.json  host_paths.json
AGENTS.md  CLAUDE.md            # root inject targets (C62)
browser/sessions/<name>         # profiles + managed binaries (D-browser-profile)
archive/{...,unclassified/}  conflicts/<stamp>/  # migrate & quarantine output
workspaces/.trash/              # deleted-ws archive`

Ws data (strict lazy `init()` writes only `workspace.json`; domains
materialize on write): 12 dir domains (`memory rules skills modules
schedules traces routine-runs relay personas content work browser`) +
`run/{delegates,procs}` (ephemeral, never synced) + config files (`config
store workspace members sync_config mcp calendar`). No secrets live inside `<ws>/`.

### 1.2 Selection semantics

- `Host.select_workspace(strict=False)`: `AGENTS_WORKSPACE` set → must exist
  (else error); unset → 0 ws = create `default`, 1 ws = use it, >1 ws =
  `agents.json -> default_workspace` or existing `default` (with
  `strict=True` raising `ValueError`).
- `Host.workspace(id)` never reads env; `Host.from_env()` only `AGENTS_HOST_DIR`.
- Entry point pins the workspace once; children receive role-scoped `ws.env()`.

### 1.3 Env hygiene & roles (the anti-leak system)

`Workspace.env(..., role="default"|"turn"|"tool"|"acp"|"container")` pops **every**
owned resolver key before setting this workspace's paths, strips infra secrets
(`INFRA_SECRET_KEYS`, `AGENTS_SYNC_TOKEN`) from non-default roles, isolates
provider keys by `provider_key_env` on turns/containers, and strips LLM & VCS
tokens on `"tool"` and `"acp"` boundaries.

### 1.4 Domain overrides = test/debug only

`AGENTS_MEMORY_DIR` & friends apply only to the entry-selected ws, emit
`UserWarning`, never apply to `Host.workspace()`. Contract level C11.

### 1.5 Secrets (Host Secret Vault)

Secrets live host-side under `<host>/secrets/` (`0700` dir, `0600` files:
`host.json`, `ws-<id>.json`), optionally backed by OS keyring or `/run/secrets`:
- **Host infra** (`RELAY_*`, `TELEGRAM_BOT_TOKEN`, `WEBHOOK_SECRET`, etc.):
  always `ENV > /run/secrets > keyring > host.json` (never workspace files).
- **Provider / workspace keys**: default `ws-<id>.json > keyring > host.json > ENV`,
  flipped to `ENV > /run/secrets > ws-<id>.json > ...` via `AGENTS_SECRETS_ORDER=env-first`.
- `expand_secrets(..., trusted=True)` expands `${VAR}` only in host-owned
  `<host>/mcp.json` and `<host>/mcp/ws-<id>.json` (never untrusted `<ws>/mcp.json`).

### 1.6 Identity / membership

`<host>/users.json` (DID users, agents-keys owns) —
`<ws>/members.json` (user+role, `members.py`, denied to terminal tools) —
`<ws>/personas/<p>/` (`persona.json`, sessions, `inbox.py` task handoffs).

### 1.7 Lifecycle ops

`create / delete` (→ `.trash`, `--hard` removes, refuses env-selected ws),
`migrate` (legacy→layout; merge-not-abort; `archive/unclassified/`; conflicts
quarantine; rollback log), `secrets set|unset|list`, `doctor`, `gc --older-than`,
CLI `create|list|show|env|info|path|migrate|delete|secrets|doctor|gc`.

### 1.8 Explicitly NOT owned

- No daemon, no network, no persona orchestration logic inside this package.
- Host desktop app state lives at `<host>/clients/desktop/` (`instances.json` /
  `instances/`, `settings.json`); legacy `~/.klanker` is retired and migrated.
- Repo-local `<repo>/.agents/` — project memory owned by agents-memory.

## 2. What it could additionally decide (proposal backlog)

| Candidate | Question | Lean |
|---|---|---|
| Secret precedence mode | file-first vs env-first on servers | **add `AGENTS_SECRETS_ORDER`** (see §3.2) |
| Ws-bound browser profile | `AGENTS_BROWSER_PROFILE` per ws (`browser/sessions/<name>`) | yes, ws field + env fallback |
| Retention/quotas | traces/routine-runs prune policy per ws | later |
| `docs/` as domain | agents-docs parked; catch-all already lands it in ws | add when docs revives |
| `voice.json` keys → secrets.json | voice stores provider keys in its own file | migrate to `resolve_secret` |
| `AGENTS_WORKDIR` policy | allowed_roots already models escape set | keep |
| Relay secret storage | `~/.klanker/relay-secret` pin vs host run/ | keep pin (cross-serve sharing is the point) |

## 3. Gaps found + fixed in this pass

1. **Migrate aborted on half-migrated hosts** → merge semantics, conflicts dir.
2. **`providers/` missing from host keep-list** → catch-all would have swept it.
3. **`AGENTS.md`/`CLAUDE.md` were in `_WS_FILES`** → NAMING C62 says host; fixed.
4. **Rogue browser profiles** (`~/.agents/browser-*`) → consolidated to
   `browser/sessions/<name>`; optional hardening: `CDPClient(user_data_dir=)`
   outside `browser_home()` → warn/refuse (proposed, not implemented).
5. **agents-browser wrote inject into `<ws>/AGENTS.md`** → host root now.
6. **`voice.json`/`voice_inbox` hardcoded host legacy paths** → host root +
   `run/voice_inbox`, env overrides `AGENTS_VOICE_CONFIG`/`AGENTS_VOICE_INBOX`.
7. **`bg-sessions` at host root** → `host/run/bg-sessions`.
8. **Tauri spawned serve inherited ambient `AGENTS_WORKSPACE`** → entry ws now
   always pinned explicitly.
9. **agents-brain/lolaplex used `AGENTS_ROOT`** → `Host.from_env()` with
   legacy env fallback.

## 4. Standalone-operation matrix (repo works without full suite?)

Every consumer repo already declares `agents-workspace>=0.1.0` as a hard
dependency — so "no workspace installed" is not reachable via pip; installing
e.g. `agents-memory` alone still pulls the path contract in and auto-creates
`~/.agents/workspaces/default` on first use. That is the intended answer:
**the contract lib is the always-present floor**, not an optional layer.
Duplicating flat-path fallbacks per repo is how the host root got dirty;
do not reintroduce them.

| Repo | ws dep | Without workspace lib | Standalone UX today |
|---|---|---|---|
| agents-memory | hard | n/a (dep pulls it) | 1 ws auto → works |
| agents-traces | hard | n/a | 1 ws auto → works |
| agents-calendar | hard | n/a | 1 ws auto → works |
| agents-voice | hard | n/a | host-level anyway → works |
| agents-docs | hard | n/a | ws `docs/` or env override |
| agents-keys | hard | n/a | host-level → works |
| agents-relay | hard | n/a | serve creates default ws |
| agents-harness | hard | n/a | via klanker/entry |
| agents-browser | hard | n/a | host profile + ws artifacts |
| agents-sync | hard | n/a | ws `sync_config.json` |
| agents-brain | soft (try/except) | `~/.agents` flat inspect | doctor falls back |
| lolaplex | soft (try/except) | `~/.agents` flat | meta pkg, falls back |

Soft-import stays only for meta/inspection tools (brain, lolaplex). Domain
consumers keep the hard dep — the floor is ~1 stdlib module, not a burden.

## 5. Secrets on servers/containers — the actual risk model

`secrets.json` plaintext at host root is the `~/.ssh` model: correct for a
developer host where the threat boundary is the OS user. Wrong for images:
a baked-in file overrides env silently (file-first precedence) and travels
with volumes.

**Proposal (concrete, small):**

1. `resolve_secret` gains order control:
   `AGENTS_SECRETS_ORDER=env-first` flips precedence to
   env → `<ws>/secrets.json` → `<host>/secrets.json`. One env var, one
   function, no format change. Container/compose sets it once.
2. Deployment rule (docs + `klanker doctor` warning):
   containers set secrets via env only; `secrets.json` never shipped in
   images or sync'd. Add `secrets.json` to agents-sync's never-sync list.
3. Long-term optional: `keyring` backend behind
   `AGENTS_SECRETS_BACKEND=keyring|file` — only if a real requirement shows
   up; the file store stays the portable default.

## 6. Why parallel doesn't bite (invariant checklist)

- One serve per host: port adopt-or-spawn + shared `relay-secret` pin.
- Ws = the only isolation boundary; every domain is ws-relative or host-
  infra, never ambient-global.
- Child envs pass through `ws.env()` which strips all owned keys → a turn
  cannot inherit another workspace's identity. Fixed in code, not convention.
- Locks: `run/migrate.lock`, `run/serve.pid`, `schedules/.tick.lock`,
  `.sync.lock` — all inside their level's dir.
- `~/.klanker` holds only app-scoped state (registry, UI settings, shared
  bearer) — orthogonal to host data by design.

## 7. Remaining open questions for the merge

- Should `CDPClient` refuse `user_data_dir` outside `browser_home()`?
  (prevents rogue `browser-*` dirs forever; breaks exotic setups)
- `voice.json.keys` → `secrets.json` consolidation: voice-specific or
  shared `LLM_API_KEY`-style naming per provider?
- `docs/` domain promotion when agents-docs revives.
- `archive/`/`conflicts/` retention policy (manual prune vs TTL).
