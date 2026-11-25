---
type: belief
id: frag-exported-workspace-identity-must-resolve
provenance: agents-bench tau2race substrate hardening - delegate/schedule failures on unmigrated hosts
ts: 2026-11-25
---

# Exported workspace identity must resolve in the child

Any identity `Workspace.env()` exports to a child process must be one the
child can resolve. `AGENTS_WORKSPACE` is a lookup key, not a label: children
pass it to `select_workspace`, which refuses ids that were never registered.

The failure that planted this belief: on an unmigrated legacy host the host
itself resolves as an ad-hoc workspace whose id is the directory name
(`.agents`). Exporting it made every child — delegates, `schedule.add`,
approval scoping — die at startup with "unknown workspace id", on exactly
the hosts legacy compatibility exists to serve. The correct behavior for
that case is to export nothing: pre-migration children re-derive legacy
layout from `AGENTS_HOST_DIR`, which is what they always did.

The general ruling: an ad-hoc or derived workspace identity is for the
process that derived it. Only registered identities may cross a process
boundary. When in doubt, export the inputs to derivation (`AGENTS_HOST_DIR`),
never the derived artifact.

Open question: should ad-hoc workspaces carry a marker bit so no consumer
has to rediscover this rule by shape inspection?
