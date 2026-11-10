## Naming & hierarchy spec (body of card C01)

Status: agreed with Felix on 10 Nov 2026 unless marked *(proposal, awaiting Felix)*. Updates 10.11. 19:31 (Felix), 10.11. (Klanker, technical) and 10.11. (Felix, 'rest passt auch') are recorded in the header. Host folder name, `<host>/users.json` + `<ws>/members.json`, D-rules-naming and D-project-store are decided (Felix, 10.11.). D-domain-overrides and concrete `_FILE` env names (D-file-suffix) are decided (Klanker, technical, 10.11.). decided (Felix, 10.11.): identity.json moves to agents-keys. keys holds users.json/DIDs; relay checks only members.json; sync only merges; traces reads identity via keys, and without keys uses its own local ID. P1 work lives on agents-keys#1 (own + migrate) and agents-traces#1 (read + local-ID fallback).

### Levels (one term = one level)

| Level | Where | Selected by | Contains |
| --- | --- | --- | --- |
| **Host** | `AGENTS_HOST_DIR`, default `~/.agents` *(decided (Felix, 10.11.): '.agents' stays, level named 'Host')*, container `/data/.agents` | one `serve` per host | infra only: `keys/` (global, Felix), `secrets.json` (global; optional `<ws>/secrets.json` override, decided (Klanker, technical, 10.11.) D-secrets), `run/` (`serve.pid`, prompt, locks), `cache/`, `logs/`, `clients/`, `<host>/agents.json` (decided (Klanker, technical, 10.11.) D-config), `<host>/mcp.json` (decided (Klanker, technical, 10.11.) D-mcp-level), `host_paths.json`, browser profile (decided (Klanker, technical, 10.11.) D-browser-profile). **No** host-wide `memory/` or `skills/` (decided Felix 10.11. 19:31, D-host-layer). decided (Felix, 10.11.): `identity.json` -> `<host>/users.json` (alias->user, + DID). decided (Felix, 10.11.): identity.json moves to agents-keys; traces reads via keys, and without keys uses its own local ID. Later cross-workspace overview is a read-only search with explicit grant, not a storage level. |
| **Workspace** | `<host>/workspaces/<id>/` | `AGENTS_WORKSPACE=<id>` (id only), passed per call | shared `memory/` for all its personas (optional private notes); `skills/ modules/ rules/ schedules/ routine-runs/ relay/ traces/ calendar.json store.db workspace.json personas/` `work/` (API default workdir) (+ `content/`, browser *artifacts* in ws, decided (Klanker, technical, 10.11.) D-browser-profile); optional `<ws>/secrets.json` and `<ws>/mcp.json` overlay; `sync_config.json` at ws (decided (Klanker, technical, 10.11.) D-sync-config-level). decided (Felix, 10.11.): `memory/` = knowledge only, `memory/rules/HARD.md` + `*.mdc` together in `<ws>/rules/`; new `<ws>/members.json` (user+role); `identity.json` moves off the workspace to `<host>/users.json`. |
| **Persona** | `<ws>/personas/<id>/persona.json` | `AGENTS_PERSONA=<id>` | personas are equal, no hierarchy (a role e.g. CoS = a persona with a role description); first persona `default` is created with the workspace (renamable); inbox + explicit task ownership for handoffs; optional private notes; manifest + prompt addition (P2) |
| **Session** | `<ws>/personas/<p>/sessions/<ses>` | API/CLI only | id = ULID (decided (Klanker, technical, 10.11.) D-session-id); key = workspace + user + persona; `channel` and `workdir` are attributes (P2) |
| *workdir* | any directory | `AGENTS_WORKDIR`, `--workdir` | NOT a level: cwd attribute of a session |

```
~/.agents/                      # Host (AGENTS_HOST_DIR; container /data/.agents)
                                # decided (Felix, 10.11.): '.agents' stays, level named 'Host'
  keys/  secrets.json  run/  cache/  logs/  clients/  host_paths.json
  agents.json  mcp.json         # D-config, D-mcp-level (Klanker technical)
  users.json                    # decided (Felix, 10.11.) (from identity.json; alias->user, + DID)
  browser/                      # profile on host (D-browser-profile); artifacts stay in the workspace
  workspaces/<ws>/              # AGENTS_WORKSPACE=<id>; shared memory for all personas
    memory/                     # decided (Felix, 10.11.): knowledge only (HARD.md leaves for rules/)
    skills/ modules/ rules/ schedules/ routine-runs/
    relay/ traces/ calendar.json store.db workspace.json work/
    secrets.json  mcp.json      # optional overrides/overlays
    members.json                # decided (Felix, 10.11.) (user+role)
    personas/<p>/               # AGENTS_PERSONA=<id>; equal; first is 'default'; optional private notes; inbox
      persona.json
      sessions/<ses>            # ULID id; key ws+user+persona
```

### Rules
1. Names ending in `_DIR` are directory paths; names without suffix are ids. File-path vars use the `_FILE` suffix: `AGENTS_USERS_FILE` (alias `AGENTS_IDENTITY_PATH`, points to `<host>/users.json`), `AGENTS_KNOWN_DIDS_FILE` (alias `AGENTS_KNOWN_DIDS`), `AGENTS_MCP_CONFIG_FILE` (alias `AGENTS_MCP_CONFIG`), `AGENTS_MCP_CACHE_FILE` (alias `AGENTS_MCP_CACHE`), `AGENTS_SYNC_CONFIG_FILE` (alias `AGENTS_SYNC_CONFIG`) — alias 1 release each (decided (Klanker, technical, 10.11.) D-file-suffix).
2. decided (Felix, 10.11.): host folder name — '.agents' stays, level named 'Host' in docs/env (`AGENTS_HOST_DIR`). The term is not `.klanker`. The term "home" disappears from code and output.
3. All aliases last 1 release (decided (Klanker, technical, 10.11.) D-alias-policy): `AGENTS_HOME` is a deprecated alias of `AGENTS_HOST_DIR` (warning), then removal as a breaking change under `[Unreleased]`. Same policy for every other alias.
4. Removed/migrated: `AGENTS_WORKSPACE_ROOT`, `AGENTS_WORKSPACE_DIR` (-> `AGENTS_WORKDIR`), `AGENTS_WORKSPACES_DIR`, `AGENTS_DIR`, `~/.agents-workspaces`, `~/.agents-relay`, `~/.klanker/daemon.pid`.
5. One `serve` per host. The workspace is passed explicitly per call; serve never switches `os.environ`. Env vars are only for child processes and at CLI entry.
6. Domain overrides (`AGENTS_MEMORY_DIR` etc.) remain only for test/debug, apply only to the workspace chosen at CLI start, and emit a warning; serve ignores them per call (decided (Klanker, technical, 10.11.) D-domain-overrides).
7. Keys stay global on the host (Felix). Provider secrets are global + optional `<ws>/secrets.json` override (decided (Klanker, technical, 10.11.) D-secrets).
8. Host is infra only (keys, secrets, run, cache, logs). No host-wide memory/skills (decided Felix 10.11. 19:31, D-host-layer). Later: read-only cross-workspace search with explicit grant, not a storage level. Workspace memory is shared by all its personas; a persona may have optional private notes.
9. API: `/v1/workspaces/{ws}/personas/{p}/sessions/{s}`; `/v1/turn` takes `{workspace, persona, session, workdir}` (ids only for workspace/persona).
10. agents-docs is archived and out of the plan (remove leftovers).
11. First start creates a normal workspace `default` (renamable/deletable, no special role). If no workspace is given: use the only existing workspace; if several exist, error (D-default-ws, decided Felix 10.11. 19:31).
12. If no workdir is given: CLI = cwd, API = `<ws>/work` (D-workdir-default, decided Felix 10.11. 19:31).
13. Personas are equal (no hierarchy). A role (e.g. CoS) is a persona with a role description. First persona `default` is created with the workspace. Explicit handoffs: inbox per persona + clear task ownership (D-persona-default, P2, decided Felix 10.11. 19:31).

### Variable / name table (old -> new)

| Old | New | Level | Owner repo(s) | Action | Card |
| --- | --- | --- | --- | --- | --- |
| `AGENTS_HOME` | `AGENTS_HOST_DIR` | Host | agents-workspace (+ browser, keys) | alias 1 release, then remove | C10, C63, C64 |
| `AGENTS_WORKSPACE_ROOT` | `AGENTS_HOST_DIR` + `AGENTS_WORKSPACE=<id>` | Host/Workspace | agents-workspace, klanker entrypoint, routine | remove + migrate | C10, C50, C20 |
| `AGENTS_WORKSPACES_DIR`, `~/.agents-workspaces`, `/data/.agents-workspaces` | fixed `<host>/workspaces/<id>` | Host | agents-workspace, klanker entrypoint | remove + migrate | C10, C50, C80 |
| `AGENTS_DIR` | `AGENTS_WORKSPACE=<id>` | Workspace | agents-sync | remove | C65 |
| — (new) | `AGENTS_WORKSPACE` (id) | Workspace | agents-workspace | new | C10 |
| — (new) | `AGENTS_PERSONA` (id) | Persona | agents-workspace, agents-harness | new (P2 consumer) | C12, C200 |
| `AGENTS_WORKSPACE_DIR` | `AGENTS_WORKDIR` | session attr | agents-workspace, klanker, terminal, harness docs | alias 1 release | C10, C24, C73 |
| `AGENTS_WORKDIR` (old default `<root>/workspace`, container `/data/workspace`) | `AGENTS_WORKDIR`, default CLI = cwd, API = `<ws>/work` (decided Felix 10.11. 19:31, D-workdir-default) | session attr | agents-workspace, klanker | keep | C10, C50 |
| `AGENTS_MEMORY_PATH` | `AGENTS_MEMORY_DIR` | Workspace (test-only) | agents-workspace, agents-memory | rename + alias | C70 |
| `AGENTS_RELAY_STATE` | `AGENTS_RELAY_DIR` | Workspace (test-only) | agents-workspace, agents-relay | rename + alias | C70, C71 |
| — | `AGENTS_RULES_DIR` | Workspace (test-only) | agents-workspace, agents-memory | new | C70 |
| `AGENTS_SKILLS_DIR`, `AGENTS_MODULES_DIR`, `AGENTS_SCHEDULES_DIR`, `AGENTS_TRACES_DIR`, `AGENTS_ROUTINE_RUNS_DIR`, `AGENTS_PERSONAS_DIR` | unchanged | Workspace (test-only; never global) | workspace, harness, memory, traces, klanker | keep; remove global exports | C11, C21, C50 |
| `AGENTS_IDENTITY_PATH` | `AGENTS_USERS_FILE` (alias 1 release) points to `<host>/users.json` (decided (Klanker, technical, 10.11.) D-file-suffix). Membership file is `<ws>/members.json` (decided (Felix, 10.11.)). | Host (`users.json`) + Workspace (`members.json`) | agents-workspace, agents-keys, agents-traces | rename + alias / move | C11, C31, C64 |
| `AGENTS_KEYS_DIR` | unchanged, default `<host>/keys` | Host | agents-keys | keep | C64 |
| `AGENTS_KNOWN_DIDS` (`$HOME/.agents/known-dids.jsonl`) | default `<host>/keys/known-dids.jsonl`; env `AGENTS_KNOWN_DIDS_FILE` (alias `AGENTS_KNOWN_DIDS`, 1 release) (decided (Klanker, technical, 10.11.) D-file-suffix) | Host | agents-keys, agents-sync | move + rename | C64, C65 |
| `AGENTS_MCP_CONFIG` (`~/.agents/mcp.json` / `<ws.root>/mcp.json`) | `<host>/mcp.json` + optional `<ws>/mcp.json` overlay (decided (Klanker, technical, 10.11.) D-mcp-level); env `AGENTS_MCP_CONFIG_FILE` (alias `AGENTS_MCP_CONFIG`, 1 release) (D-file-suffix) | Host + optional ws overlay | agents-harness, klanker, calendar, traces | move-level + rename | C61, C67, C66 |
| `AGENTS_MCP_CACHE` (`~/.agents/mcp-tools-cache.json`) | `<host>/cache/mcp-tools-cache.json`; env `AGENTS_MCP_CACHE_FILE` (alias `AGENTS_MCP_CACHE`, 1 release) (decided (Klanker, technical, 10.11.) D-file-suffix) | Host | agents-harness | move + rename | C61 |
| `AGENTS_CONTENT_DIR` (`~/.agents/harness-content`) | default `<ws>/content` | Workspace | agents-harness | move | C61 |
| `~/.agents/config.json` (timezone fallback), `config.json` | `<host>/agents.json` (decided (Klanker, technical, 10.11.) D-config) | Host | agents-harness, agents-workspace | move | C61, C10 |
| `AGENTS_SKILLS_EXTRA` | unchanged | Workdir | agents-harness | keep | C72 |
| `AGENTS_WORKSPACE_ALLOW` | `AGENTS_WORKDIR_ALLOW` | session attr | agents-harness, agents-terminal | alias 1 release | C72, C73 |
| `AGENTS_WORKSPACE_LOCK` | `AGENTS_WORKDIR_LOCK` | session attr | agents-terminal, agents-harness, klanker | alias 1 release | C73, C72, C24 |
| `AGENTS_BROWSER_SESSION`, `~/.agents/browser` | `AGENTS_BROWSER_PROFILE` (alias 1 release); profile on the host, artifacts in the workspace (`<ws>/browser`) (decided (Klanker, technical, 10.11.) D-browser-profile) | Host profile + ws artifacts | agents-browser | rename + alias | C63 |
| `RELAY_STATIC_DIR` vs `AGENTS_RELAY_WEB_DIR` | `AGENTS_RELAY_WEB_DIR` (decided (Klanker, technical, 10.11.) D-relay-web-var) | Host | agents-relay | rename + alias | C71 |
| `GATEWAY_SECRET/HOST/PORT`, `GatewayConfig` | `RELAY_SECRET/HOST/PORT` | Host | agents-relay, klanker Dockerfile | alias 1 release | C71, C50 |
| `AGENTS_SESSION_ID` (one per process) | session id = ULID; key stays ws+user+persona (decided (Klanker, technical, 10.11.) D-session-id). Traces import-time store is P1 (D-traces-p1, C31) | Session | agents-traces | P2 (id/key) / P1 (store) | C201, C31 |
| `AGENTS_SYNC_CONFIG`, `sync_config.json` | keep at workspace level (decided (Klanker, technical, 10.11.) D-sync-config-level); env `AGENTS_SYNC_CONFIG_FILE` (alias `AGENTS_SYNC_CONFIG`, 1 release) (D-file-suffix) | Workspace | agents-sync | keep + rename | C65 |
| `node_id`, `host_base_root=~/Coding` | host config, no personal default | Host | agents-sync | move | C65, C100 |
| `CALDAV_*` env, `<root>/.env` | host `secrets.json` + optional `<ws>/secrets.json` override (decided (Klanker, technical, 10.11.) D-secrets), env = host fallback | Host | agents-calendar | move | C66 |
| `~/.klanker/daemon.pid` | `<host>/run/serve.pid` | Host | klanker | move | C41, C80 |
| `klanker_prompt.txt` (ws root) | `<host>/run/` | Host | klanker | move | C41 |
| `<cwd>/.agents/repl/prefs.json` | `<host>/clients/shell/prefs.json` | Host | klanker | move | C67 |
| `~/.cache/agents` (updates.py, 7 repos) | `<host>/cache/updates/` | Host | 7 repos | move | C68 |
| `<root>/clients`, `~/.client` | `<host>/clients/` | Host | agents-relay | move + migrate | C69, C80 |
| `~/.agents-relay` | `<ws>/relay` | Workspace | agents-relay | remove fallback + migrate | C40, C80 |
| `host_paths.json` (in memory dir) | `<host>/host_paths.json` | Host | agents-memory | move | C62 |
| `<root>/AGENTS.md`, `CLAUDE.md` inject targets | host; editor targets live in user home (decided (Klanker, technical, 10.11.) D-inject-level) | Host (user home) | agents-memory | move | C62 |
| `memory/rules/HARD.md` vs `<ws>/rules/*.mdc`; `<repo>/.agents/memory` | decided (Felix, 10.11.): HARD.md + `*.mdc` together to `<ws>/rules/`, `memory/` = knowledge only (D-rules-naming); `<repo>/.agents` stays (D-project-store) | Workspace / project | agents-memory | rename | C75 |
| `/data/.agents/docs`, docs modules, sync docs domain | removed | — | klanker, harness, sync | remove | C50, C61, C65 |
| `AGENTS_OPENSHELL_DIR`, `OPENSHELL_STATE_DIR`, `AGENTS_SANDBOX_CACHE` | unchanged (optional under `<host>/cache`) | Host | agents-terminal | keep | C68 |
| `AGENTS_SYNC_VAULT_DIR` (`~/.agents-sync-vault`) | unchanged (sync server) | Host | agents-sync | keep | C65 |
| `AGENTS_PROVIDERS_DIR`, `AGENTS_APPROVAL_MODE/CMD/TIMEOUT`, `KLANKER_TELEGRAM_OPEN`, `KLANKER_USER`, `RELAY_WEB_USER` (default `web`), `AGENTS_RELAY_MAX_JOBS(_PER_CHAT)/APPROVER/ATTACH_DIR/ALLOW_ANYONE`, `AGENTS_BROWSER_BIN/PORT/HEADLESS`, `CHROME_PATH`, `BROWSER_PATH`, tuning vars (`LLM_*`, `TELEGRAM_*`, `GIT_*`, timeouts) | unchanged | Host/Session | various | keep | — |
| `KLANKER_CHANNEL` (written to os.environ) | turn/session argument | Session | klanker | keep name, stop env write | C21 |

### CLI / HTTP / Python names

| Old | New | Card |
| --- | --- | --- |
| `klanker --workspace <dir>` (root/prompt/chat) | `--workdir <dir>` (old form 1 release with warning) | C24 |
| `klanker routine add/say --workspace <id/label/path>` | `--workspace <id>` | C24 |
| `klanker shell` (no workspace) | `--workspace <id>` | C24 |
| `python -m runner.loop` (no workspace) | `--workspace <id>`; `--persona <id>` from `<ws>/personas` (P2) | C23, C200 |
| `agents-workspace init --root --label --persona` | `create <id> [--label] [--persona]`, `list`, `show`, `env`, `migrate` | C13, C80 |
| `/v1/turn`, `/v1/alert`, `/v1/chat/stream` `workspace` = path/label | id only; new `workdir` field | C22 |
| `POST/GET /v1/workspaces` | + personas/sessions routes (P2) | C202 |
| `legacy_root()` | `host_dir()` / `Host` | C10 |
| `Workspace.root`, `explicit_root`, `source_env` | `Workspace.dir`, `Host` type, no `explicit_root` | C10 |
| `Workspace.claude` | always `~/.claude` | C10 |
| klanker `apply_workspace()`, `evidence.agents_home()`, `evidence.workspace_path()` | `apply_workdir()`, `workspace_dir()`, `workdir()` | C24 |
| harness `workspace_root()` | `workdir_root()` | C72 |
| terminal `work_root()`, `workspace_dir()`, `workspace_root()`, `_agents_home()` | `workdir()`, `workdir_root()`, resolver | C73, C60 |
| relay `agents_home()`, `clients_dir()`; calendar `agents_home()`; traces `_agents_home()`; sync label `Agents Home:` | `host_dir()` / `workspace_dir()`; `Workspace:` | C69, C66, C74, C65 |
| memory `_WS`, `AGENTS_HOME`, `USER_MEMORY`, `AGENTS_RULES`, `INDEX_DIR` module constants | `MemoryStore(workspace)` | C30 |
| traces `DEFAULT_PATH`, `_global_store` | per-workspace stores | C31 |
| klanker `routine._WORKSPACE_ENV_KEYS` | removed; `tick(ws)` | C20 |
| klanker `overlay` `AGENTS_MODULES_DIR` setdefault, `publish_modules_dir` | removed; per-workspace overlay | C21 |

### Pinned SHAs

Pinned dev heads (inventory, 10 Nov 2026 09:55 Europe/Berlin): klanker 7d96f755, agents-workspace eb842bc9 (dev=main), agents-memory 130ffef8, agents-harness 267cd48d, agents-relay 22a30456, agents-calendar 0a211ec5, agents-traces 19211708, agents-terminal b625f025, agents-browser 3c3823ad, agents-keys 878ae796, agents-sync 50082cfd. QA (HANDOFF.md) refs are at the foundation SHAs (klanker 55191a9, memory a968a2a9, traces 4c1e8103, harness ca3144e3, relay 4ff7450a, calendar 6c3e018a, terminal dbaf4399, sync 1dd6a48a) and are marked `QA@`.
