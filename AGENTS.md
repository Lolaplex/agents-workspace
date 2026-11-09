# AGENTS.md — agents-workspace Developer Guidelines

## Architecture Principles
- **One root for every agents domain**: memory, traces, schedules, personas, relay state.
- **Pure Python standard library**: stdlib first, zero heavy dependencies.
- **Precedence**: explicit env, then workspace default, then global fallback.
- **No daemons, no network, no keyring**.
- **Domains**:
  - `memory`: `AGENTS_MEMORY_PATH` -> `root/memory`
  - `rules`: `root/rules`
  - `skills`: `AGENTS_SKILLS_DIR` -> `root/skills`
  - `modules`: `AGENTS_MODULES_DIR` -> `root/modules`
  - `schedules`: `AGENTS_SCHEDULES_DIR` -> `root/schedules`
  - `traces`: `AGENTS_TRACES_DIR` -> `root/traces`
  - `routine-runs`: `AGENTS_ROUTINE_RUNS_DIR` -> `root/routine-runs`
  - `relay`: `AGENTS_RELAY_STATE` -> `root/relay`
  - `personas`: `AGENTS_PERSONAS_DIR` -> `root/personas`
  - `claude`: `root/claude` when explicit, else `~/.claude`
  - `identity`: `AGENTS_IDENTITY_PATH` -> `root/identity.json`
  - `config`: `root/config.json`
  - `secrets`: `root/secrets.json`
  - `store`: `root/store.db`

## Commands
- Run test suite: `python -m pytest` or `python tests/run_all_tests.py`
- Test CLI: `agents-workspace info` or `python -m agents_workspace info`
