# Personas (P2)

Status: decided rules, implemented. This document is the P2 persona
home; it extends, never contradicts, the P1 naming spec in
[docs/NAMING.md](NAMING.md) (body of card agents-workspace#1).

## Levels (fixed by P1)

    Host > Workspace > Persona > Session

    <host>/workspaces/<ws>/personas/<p>/sessions/<s>

- **Host** — infrastructure only (`keys/`, `secrets.json`, `run/`,
  `cache/`, `logs/`, `clients/`, `agents.json`, `mcp.json`,
  `users.json`, browser profiles). No workspace data lives here.
- **Workspace** — the data boundary. `memory/` is shared by all its
  personas; a persona may keep optional private notes, which are not a
  substitute for shared memory.
- **Persona** — `<ws>/personas/<id>/persona.json`. Selected per call
  via `AGENTS_PERSONA=<id>` or `--persona <id>` (ids only).
- **Session** — `<ws>/personas/<p>/sessions/<ses_...>.json`. Keyed by
  `(workspace, user, persona)`; id is a `ses_`-prefixed ULID
  (D-session-id). `channel` and `workdir` are session attributes —
  `workdir` is a cwd, not a level.

## Persona rules (decided)

1. **Personas are equal.** There is no hierarchy and no hidden
   default-persona privilege. `workspace.json` `default_persona` is
   initial-selection metadata only, not a layer.
2. **First persona is `default`.** Creating a workspace creates
   `<ws>/personas/default/persona.json`. It is renamable and carries
   no special role.
3. **A role is description text.** A "Chief of Staff" is a persona
   with a `role` field in `persona.json`, not a superior level.
4. **Loading order.** `runner.loop` reads
   `<ws>/personas/<id>/persona.json` first and falls back to package
   personas; a workspace definition overrides a package definition of
   the same id.
5. **Explicit handoffs.** Tasks move between personas via the inbox:
   a task is one JSON file in exactly one
   `<ws>/personas/<p>/inbox/<tsk_...>.json`. The owner is the location.
   `handoff` moves the file to the target inbox and appends a
   `handoff` event; a `--from` mismatch is refused. No persona routes
   through `default` implicitly.

## API surface (implemented)

- `GET /v1/personas?workspace=<id>` — workspace-scoped persona list
- `GET/POST /v1/workspaces/<ws>/personas[/<p>]` — create/list personas
- `GET/POST /v1/workspaces/<ws>/personas/<p>/sessions[/<s>]` — session
  list (`?user=` filter), mint, history
- `GET/POST /v1/workspaces/<ws>/personas/<p>/inbox`,
  `POST .../inbox/<task>/handoff {to, note}` — task inbox + ownership
  transfer
- `/v1/turn` accepts `{workspace, persona, session, workdir}`; no
  `workdir` means the API uses `<ws>/work`
- `klanker persona list|add|inbox|task|handoff` — CLI equivalents

## Identity split (fixed)

agents-keys owns identity (`<host>/users.json` + DIDs); traces reads
it and falls back to a local id without keys. Relay checks
`<ws>/members.json` (user + role). Personas are not users: a user can
hold sessions under several personas of the same workspace.
