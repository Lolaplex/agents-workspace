# Perimeter — what actually separates the layers

Status: agreed boundary model. Complements `NAMING.md` (what each level holds).
This file says **how** each level is separated and which mechanism is a real
kernel boundary vs. a hint a child process can strip.

## Layer table

| Layer | Where | Contents | Boundary kind |
| --- | --- | --- | --- |
| **Host** | `AGENTS_HOST_DIR` (default `~/.agents`) | keys, `secrets/{host,ws-<id>}.json`, run (`serve.json`, `approvals/`), cache, logs, `clients/desktop/`, providers, `agents.json`, `mcp.json`, `mcp/ws-<id>.json`, `approval-allowlist.json`, users.json, `browser/sessions/<name>` | kernel (container scope) + parent-enforced (env construction & vault) |
| **Workspace** | `<host>/workspaces/<id>/` | lazy domains (memory, skills, modules, rules, schedules, routine-runs, relay, traces, personas, content, work, browser artifacts) + ephemeral `run/{delegates,procs}` + config files (`config.json`, `mcp.json`, `members.json`, `store.db`); zero secrets | kernel when `isolation=container`; env-hint otherwise |
| **Persona** | `<ws>/personas/<id>/persona.json` | role, prompt addition, optional private notes | attribute only — no security boundary |
| **Session** | `ses_…` under a persona | one turn's context, channel, workdir | attribute only — no security boundary |

`*workdir*` is not a layer: it is the cwd attribute of a session
(`AGENTS_WORKDIR`). It participates in the jail, it owns nothing.

## Boundary kinds

| Mechanism | Kind | Holds because |
| --- | --- | --- |
| `agents-terminal` openshell driver | **kernel** | sandbox/container fs isolation; the child has no host fs to escape to |
| ws `isolation=container` (`runtime.py`) | **kernel** | only `ws.dir` is mounted at `/ws`; other workspaces do not exist inside |
| `AGENTS_WORKDIR_LOCK` + `allowed_roots()` | **env-hint** | the lock flag and allow list live in child env; `env -u AGENTS_WORKDIR_LOCK <cmd>` strips them |
| `AGENTS_DELEGATE_DEPTH` / `AGENTS_DELEGATE_MAX_DEPTH` | **env-hint** | depth counter travels in child env; refusal runs in the parent |
| `AGENTS_APPROVAL_MODE` / `AGENTS_APPROVAL_CMD` | **parent-enforced** | the gate runs in the parent loop process or authenticates over `/v1/approvals/` via ephemeral `AGENTS_APPROVAL_TOKEN` (`<host>/run/approvals/`); nothing in the child holds bot/webhook secrets |
| `Workspace.env(..., role=...)` key strip + re-pin | **parent-enforced** | the parent builds role-scoped child envs (`turn`, `tool`, `acp`, `container`); infra secrets never enter turns/containers, and LLM/VCS tokens never enter `"tool"` or `"acp"` |
| `TELEGRAM_ALLOWED_CHAT_IDS` / `AGENTS_RELAY_ALLOW_ANYONE` | **parent-enforced** | the allowlist is checked in the relay process before any turn exists |
| `resolve_secret` host vault + `AGENTS_SECRETS_ORDER` | **parent-enforced** | host-infra secrets are locked `ENV > /run/secrets > keyring > host.json`; workspace files never override infra keys |
| docker `-e KEY` env passthrough (`_SAFE_AGENTS_ENV`) | **parent-enforced** | parent allowlists safe keys crossing into `--cap-drop=ALL` `klanker-ws` containers; tokens/infra secrets stay blocked |

## Honest reading

- Env flags are **anti-accident only**. `AGENTS_WORKDIR_LOCK=1` stops a
  confused command from wandering off the workspace; it does not stop a
  determined child — `env -u` removes the flag, and nothing watches it.
  That is accepted: the threat model is drift, not hostility.
- Anything the child could plausibly want to defeat must live in the
  parent (approval, allowlist, env construction, secret resolution) or in
  the kernel (openshell, `isolation=container`).
- `allowed_roots()` and `denied_paths()` are the canonical jail lists
  (`agents_terminal.policy`, `runner.executor`, `runner.delegate`).
  `denied_paths()` is evaluated first and explicitly blocks `host.keys`,
  `host.secrets_dir`, `host.secrets`, `host.mcp_json`, `host.mcp_dir`,
  `host.agents_json`, `host.users_file()`, `host.approval_allowlist`,
  `ws.secrets`, and `ws.members`.

## Delegate turns (agents-workspace#18)

- A delegate/subagent child is a full `runner.loop` turn: it needs the
  live workspace, provider env, memory and traces. An ephemeral
  openshell snapshot cannot host it — that backend is upload-only and
  carries no runtime.
- **Delegates share the parent turn's privilege class.** The hard
  boundary is the deploy container (`isolation=container` / the
  Docker-perimeter deploy). In-turn isolation is advisory only.
- `AGENTS_DELEGATE_DEPTH` (+ `AGENTS_DELEGATE_MAX_DEPTH`) is an
  env-hint counter: `delegate_env()` increments it in the parent before
  the child exists and refuses past the limit. It guards against
  runaway recursion, not adversarial self-jailbreak — `env -u` strips it.
- A real in-turn overlay waits on the ExecBackend interface
  (agents-runtime#2 / harness#8). Until then delegates must run inside
  the deploy perimeter: the delegate option is a deployment rule, not a
  sandbox mechanism.
