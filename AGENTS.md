# AGENTS.md — agents-workspace Developer Guidelines

## Architecture Principles
- **One root for every agents domain**: memory, traces, schedules, personas, relay state.
- **Pure Python standard library**: stdlib first, zero heavy dependencies.
- **Precedence**: explicit env, then workspace default, then global fallback.
- **No daemons, no network, optional lazy keyring**.
- **Domains**:
  - `memory`: `AGENTS_MEMORY_DIR` -> `<ws>/memory`
  - `rules`: `AGENTS_RULES_DIR` -> `<ws>/rules`
  - `skills`: `AGENTS_SKILLS_DIR` -> `<ws>/skills`
  - `modules`: `AGENTS_MODULES_DIR` -> `<ws>/modules`
  - `schedules`: `AGENTS_SCHEDULES_DIR` -> `<ws>/schedules`
  - `traces`: `AGENTS_TRACES_DIR` -> `<ws>/traces`
  - `routine-runs`: `AGENTS_ROUTINE_RUNS_DIR` -> `<ws>/routine-runs`
  - `relay`: `AGENTS_RELAY_DIR` -> `<ws>/relay`
  - `personas`: `AGENTS_PERSONAS_DIR` -> `<ws>/personas`
  - `claude`: `~/.claude`
  - `identity`: `AGENTS_USERS_FILE` -> `<host>/users.json`
  - `config`: `<ws>/config.json`
  - `secrets`: `<host>/secrets/{host,ws-<id>}.json` (0700/0600)
  - `store`: `<ws>/store.db`

## Commands
- Run test suite: `python -m pytest` or `python tests/run_all_tests.py`
- Test CLI: `agents-workspace info` or `python -m agents_workspace info`
