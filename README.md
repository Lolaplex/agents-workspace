# agents-workspace

One root for every agents domain.

`AGENTS_HOME` grew from a memory override into a de-facto workspace root, but six
domains never followed it. This package makes the root explicit and resolves
every domain from it in one place.

```python
from agents_workspace import Workspace

ws = Workspace.from_env()
ws.root          # AGENTS_WORKSPACE_ROOT, else AGENTS_HOME, else ~/.agents
ws.workdir       # AGENTS_WORKDIR, else AGENTS_WORKSPACE_DIR, else root/workspace
ws.memory        # AGENTS_MEMORY_PATH, else root/memory
ws.traces        # AGENTS_TRACES_DIR, else root/traces
ws.schedules     # AGENTS_SCHEDULES_DIR, else root/schedules
ws.personas      # AGENTS_PERSONAS_DIR, else root/personas
```

## Domains

| Domain | Env override | Default |
|---|---|---|
| `memory` | `AGENTS_MEMORY_PATH` | `root/memory` |
| `rules` | — | `root/rules` |
| `skills` | `AGENTS_SKILLS_DIR` | `root/skills` |
| `modules` | `AGENTS_MODULES_DIR` | `root/modules` |
| `schedules` | `AGENTS_SCHEDULES_DIR` | `root/schedules` |
| `traces` | `AGENTS_TRACES_DIR` | `root/traces` |
| `routine-runs` | `AGENTS_ROUTINE_RUNS_DIR` | `root/routine-runs` |
| `relay` | `AGENTS_RELAY_STATE` | `root/relay` |
| `personas` | `AGENTS_PERSONAS_DIR` | `root/personas` |
| `claude` | — | `root/claude` when the root is explicit, else `~/.claude` |
| `identity` | `AGENTS_IDENTITY_PATH` | `root/identity.json` |
| `config` | — | `root/config.json` |
| `secrets` | — | `root/secrets.json` |
| `store` | — | `root/store.db` |

Precedence is always: explicit env, then the workspace default, then the global
fallback. `AGENTS_HOME` and `AGENTS_WORKSPACE_DIR` stay readable as legacy
aliases; new code writes `AGENTS_WORKSPACE_ROOT` and `AGENTS_WORKDIR`.

## Registry

Additional workspaces live under `AGENTS_WORKSPACES_DIR` (default `~/.agents-workspaces`). `Workspace.list_all()` returns the default workspace plus every child with a `workspace.json`. `Workspace.resolve(ref)` matches an id, label, directory name, or path. `Workspace.create(label)` mints a slug directory and initializes it.

## CLI

```bash
agents-workspace info
agents-workspace path traces
agents-workspace init --label personal
agents-workspace list
agents-workspace create --label personal
agents-workspace migrate --dry-run
```

`migrate` moves legacy `~/.agents/<domain>` directories into the workspace when
the workspace root is not `~/.agents` and the target does not exist yet.

## What this is not

- No daemon, no network, no keyring.
- No persona logic. Personas are a domain path.
- No config schema beyond `workspace.json`.
