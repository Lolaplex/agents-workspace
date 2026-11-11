# agents-workspace

Host > Workspace > Persona > Session path resolver for the Lolaplex suite.
See `docs/NAMING.md` for the canonical P1 spec and `docs/PERSONAS.md`
for the P2 persona rules (equal personas, first `default`, inbox
handoffs, session key).

```python
from agents_workspace import Host, Workspace

host = Host.from_env()          # AGENTS_HOST_DIR, else ~/.agents
ws = host.workspace("a")        # <host>/workspaces/a — never reads env overrides
ws = Workspace.from_env()       # CLI entry: AGENTS_WORKSPACE=<id> selects the ws
ws.memory                       # <ws>/memory
ws.traces                       # <ws>/traces
ws.env()                        # child env for a turn/job inside this workspace
```

The Host holds infrastructure only: `keys/`, `secrets.json`, `run/`, `cache/`,
`logs/`, `clients/`, `agents.json`, `mcp.json`, `host_paths.json`, `users.json`,
browser profile. Every Workspace lives at `<host>/workspaces/<id>` and owns the
data domains.

## Domains

| Domain | Env override (test/debug only) | Default |
|---|---|---|
| `memory` | `AGENTS_MEMORY_DIR` (alias `AGENTS_MEMORY_PATH`) | `<ws>/memory` |
| `rules` | `AGENTS_RULES_DIR` | `<ws>/rules` |
| `skills` | `AGENTS_SKILLS_DIR` | `<ws>/skills` |
| `modules` | `AGENTS_MODULES_DIR` | `<ws>/modules` |
| `schedules` | `AGENTS_SCHEDULES_DIR` | `<ws>/schedules` |
| `traces` | `AGENTS_TRACES_DIR` | `<ws>/traces` |
| `routine-runs` | `AGENTS_ROUTINE_RUNS_DIR` | `<ws>/routine-runs` |
| `relay` | `AGENTS_RELAY_DIR` (alias `AGENTS_RELAY_STATE`) | `<ws>/relay` |
| `personas` | `AGENTS_PERSONAS_DIR` | `<ws>/personas` |
| `content` | `AGENTS_CONTENT_DIR` | `<ws>/content` |
| `work` | — | `<ws>/work` |
| `browser` | — | `<ws>/browser` (artifacts; profile lives on the Host) |
| `members` | — | `<ws>/members.json` |
| `config` | — | `<ws>/config.json` |
| `secrets` | — | `<ws>/secrets.json` (overrides host `secrets.json`) |
| `store` | — | `<ws>/store.db` |
| `sync_config` | `AGENTS_SYNC_CONFIG_FILE` (alias `AGENTS_SYNC_CONFIG`) | `<ws>/sync_config.json` |
| `mcp` | `AGENTS_MCP_CONFIG_FILE` (alias `AGENTS_MCP_CONFIG`) | `<ws>/mcp.json` (overlay of host `mcp.json`) |
| `calendar` | — | `<ws>/calendar.json` |

`identity` resolves to the host users file `<host>/users.json`
(`AGENTS_USERS_FILE`, alias `AGENTS_IDENTITY_PATH`).

Domain overrides are **test/debug only**: they apply to the workspace chosen
at CLI entry (`Workspace.from_env`) and emit a `UserWarning`; `Host.workspace()`
and `list_workspaces()` never read them.

Selection without an id: the only existing workspace is used; if several
exist, `Workspace.from_env()` errors (pass `AGENTS_WORKSPACE=<id>` or
`--workspace`). First start creates a normal workspace `default` with persona
`default` — both renamable, no special role.

## Migration notes (one release)

Deprecated aliases still read, with warnings:

- `AGENTS_HOME` → `AGENTS_HOST_DIR` (a dir without `workspaces/` but with
  legacy domain dirs still resolves as the workspace itself until `migrate`)
- `AGENTS_WORKSPACE_DIR` → `AGENTS_WORKDIR`
- `AGENTS_IDENTITY_PATH` → `AGENTS_USERS_FILE`
- `AGENTS_WORKSPACES_DIR` → `<host>/workspaces`
- `AGENTS_WORKSPACE_ROOT` → ad-hoc workspace dir (no host metadata)

## CLI

```bash
agents-workspace create a --label personal   # <host>/workspaces/a + persona default
agents-workspace list
agents-workspace show a --json
agents-workspace env a                       # KEY=VALUE lines for child envs
agents-workspace info
agents-workspace path traces
agents-workspace migrate --dry-run
```

`init` remains for one release as a deprecated alias that refuses `--root`
outside `<host>/workspaces`. `migrate` moves legacy host-level domain dirs
(`<host>/memory`, `<host>/traces`, …) into the selected workspace when the
target does not exist yet.

## What this is not

- No daemon, no network, no keyring.
- No persona logic. Personas are a domain path.
- No config schema beyond `workspace.json`.
