# Perimeter — what actually separates the layers

Status: agreed boundary model. Complements `NAMING.md` (what each level holds).
This file says **how** each level is separated and which mechanism is a real
kernel boundary vs. a hint a child process can strip.

## Layer table

| Layer | Where | Contents | Boundary kind |
| --- | --- | --- | --- |
| **Host** | `AGENTS_HOST_DIR` (default `~/.agents`) | keys, secrets.json, run, cache, logs, clients, agents.json, mcp.json, users.json, browser profile | kernel (container scope) + parent-enforced (env construction) |
| **Workspace** | `<host>/workspaces/<id>/` | domains (memory, skills, modules, rules, schedules, routine-runs, relay, traces, personas, content, work, browser artifacts) + ws files (secrets.json, mcp.json, members.json, store.db) | kernel when `isolation=container`; env-hint otherwise |
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
| `AGENTS_DELEGATE_DEPTH` / `MAX_DEPTH` | **env-hint** | depth counter travels in child env |
| `AGENTS_APPROVAL_MODE` / `AGENTS_APPROVAL_CMD` | **parent-enforced** | the gate runs in the parent loop process; the model's `call_job` waits on the approver, nothing in the child reaches it |
| `Workspace.env()` key strip + re-pin (C12, `REMOVED_ENV`) | **parent-enforced** | the parent builds the child env; foreign-workspace values never cross |
| `TELEGRAM_ALLOWED_CHAT_IDS` / `AGENTS_RELAY_ALLOW_ANYONE` | **parent-enforced** | the allowlist is checked in the relay process before any turn exists |
| `resolve_secret` file precedence (`<ws>/secrets.json` > `<host>/secrets.json` > env) | **parent-enforced** | a file pins the value; stripping or overriding the env var cannot beat it |
| docker `-e KEY` env passthrough | **parent-enforced** | parent chooses which keys cross into the container; values never hit ws files |

## Honest reading

- Env flags are **anti-accident only**. `AGENTS_WORKDIR_LOCK=1` stops a
  confused command from wandering off the workspace; it does not stop a
  determined child — `env -u` removes the flag, and nothing watches it.
  That is accepted: the threat model is drift, not hostility.
- Anything the child could plausibly want to defeat must live in the
  parent (approval, allowlist, env construction, secret resolution) or in
  the kernel (openshell, `isolation=container`).
- `allowed_roots()` is the canonical allow list the lock honors
  (`agents_terminal.policy`, `runner.executor`, `runner.delegate`).
  It lists *reachable* dirs, not *safe* ones — `host.keys` and
  `host.secrets` are on it so infra keeps working; a jail is not a
  secrecy boundary.
