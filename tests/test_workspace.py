"""Host/Workspace resolver tests."""

from __future__ import annotations

import os
import tempfile
import unittest
import warnings
from pathlib import Path

from agents_workspace import Host, Workspace, host_dir


def _p(raw: str) -> Path:
    return Path(raw).expanduser().resolve()


def _host_dir(path: str) -> dict:
    return {"AGENTS_HOST_DIR": path}


class TestHostSelection(unittest.TestCase):
    def test_host_dir_env_wins(self):
        self.assertEqual(host_dir({"AGENTS_HOST_DIR": "/tmp/h"}), _p("/tmp/h"))

    def test_host_home_alias_warns(self):
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            got = host_dir({"AGENTS_HOME": "/tmp/h"})
        self.assertEqual(got, _p("/tmp/h"))
        self.assertTrue(
            any(w.category is DeprecationWarning for w in seen), "no DeprecationWarning"
        )

    def test_host_dir_beats_home(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            got = host_dir({"AGENTS_HOST_DIR": "/tmp/new", "AGENTS_HOME": "/tmp/old"})
        self.assertEqual(got, _p("/tmp/new"))

    def test_host_default(self):
        self.assertEqual(host_dir({}), Path.home() / ".agents")


class TestWorkspaceResolution(unittest.TestCase):
    def test_workspace_dir_under_host(self):
        host = Host.open("/tmp/h")
        self.assertEqual(host.workspace("a").dir, _p("/tmp/h/workspaces/a"))

    def test_workspace_id_validation(self):
        host = Host.open("/tmp/h")
        for bad in ("../x", "a/b", "a b", "", ".", "..", "x\\y"):
            with self.assertRaises(ValueError, msg=bad):
                host.workspace(bad)

    def test_first_start_creates_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            ws = host.select_workspace()
            self.assertEqual(ws.id, "default")
            self.assertTrue((Path(tmp) / "workspaces" / "default" / "workspace.json").is_file())
            self.assertTrue((Path(tmp) / "workspaces" / "default" / "personas" / "default" / "persona.json").is_file())

    def test_single_workspace_used_when_no_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("solo")
            ws = host.select_workspace()
            self.assertEqual(ws.id, "solo")

    def test_several_workspaces_error_without_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("a")
            host.create("b")
            with self.assertRaises(ValueError):
                host.select_workspace()
            self.assertEqual(host.select_workspace("a").dir, Path(tmp) / "workspaces" / "a")

    def test_unknown_id_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("a")
            with self.assertRaises(ValueError):
                host.select_workspace("nope")

    def test_env_selects_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("a")
            ws = Workspace.from_env({"AGENTS_HOST_DIR": tmp, "AGENTS_WORKSPACE": "a"})
            self.assertEqual(ws.id, "a")
            self.assertEqual(ws.dir, Path(tmp) / "workspaces" / "a")


class TestAdHocCompat(unittest.TestCase):
    def test_workspace_root_bridge(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ws = Workspace.from_env({"AGENTS_WORKSPACE_ROOT": "/tmp/ws"})
        self.assertEqual(ws.dir, _p("/tmp/ws"))
        self.assertEqual(ws.workdir, _p("/tmp/ws/work"))

    def test_workdir_env(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ws = Workspace.from_env(
                {"AGENTS_WORKSPACE_ROOT": "/tmp/ws", "AGENTS_WORKDIR": "/tmp/cwd"}
            )
        self.assertEqual(ws.workdir, _p("/tmp/cwd"))

    def test_workdir_legacy_alias(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ws = Workspace.from_env(
                {"AGENTS_WORKSPACE_ROOT": "/tmp/ws", "AGENTS_WORKSPACE_DIR": "/tmp/old"}
            )
        self.assertEqual(ws.workdir, _p("/tmp/old"))


class TestDomains(unittest.TestCase):
    def _ws(self, tmp: str) -> Workspace:
        return Host.open(tmp).workspace("a")

    def test_defaults_follow_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = self._ws(tmp)
            for name in ("memory", "traces", "skills", "modules", "schedules",
                         "routine-runs", "relay", "personas", "rules"):
                self.assertEqual(ws.domain(name), Path(tmp) / "workspaces" / "a" / name, name)

    def test_file_domains(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = self._ws(tmp)
            base = Path(tmp) / "workspaces" / "a"
            self.assertEqual(ws.members, base / "members.json")
            self.assertEqual(ws.sync_config, base / "sync_config.json")
            self.assertEqual(ws.calendar, base / "calendar.json")
            self.assertEqual(ws.workspace_file, base / "workspace.json")

    def test_host_infra_paths(self):
        host = Host.open("/tmp/h")
        self.assertEqual(host.keys, _p("/tmp/h/keys"))
        self.assertEqual(host.run, _p("/tmp/h/run"))
        self.assertEqual(host.cache, _p("/tmp/h/cache"))
        self.assertEqual(host.clients, _p("/tmp/h/clients"))
        self.assertEqual(host.users_file(), _p("/tmp/h/users.json"))

    def test_identity_on_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = self._ws(tmp)
            self.assertEqual(ws.identity, Path(tmp) / "users.json")

    def test_override_only_on_cli_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("a")
            env = _host_dir(tmp) | {
                "AGENTS_WORKSPACE": "a",
                "AGENTS_MEMORY_PATH": "/tmp/extmem",
            }
            ws = Workspace.from_env(env)
            self.assertEqual(ws.memory, _p("/tmp/extmem"))
            # The same workspace via Host never sees the process override.
            self.assertEqual(
                host.workspace("a").memory, Path(tmp) / "workspaces" / "a" / "memory"
            )

    def test_claude_is_home(self):
        ws = Host.open("/tmp/h").workspace("a")
        self.assertEqual(ws.claude, Path.home() / ".claude")

    def test_process_override_never_reaches_other_workspaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("a")
            host.create("b")
            import os as _os
            from unittest.mock import patch as _patch

            with _patch.dict(_os.environ, {"AGENTS_MEMORY_PATH": "/tmp/extmem"}):
                self.assertEqual(
                    host.workspace("b").memory,
                    Path(tmp) / "workspaces" / "b" / "memory",
                )
                self.assertEqual(
                    [str(ws.memory) for ws in host.list_workspaces()],
                    [
                        str(Path(tmp) / "workspaces" / "a" / "memory"),
                        str(Path(tmp) / "workspaces" / "b" / "memory"),
                    ],
                )
                # open() (deprecated ad-hoc) also ignores process overrides.
                ws = Workspace.open(tmp + "/adhoc")
                self.assertEqual(ws.memory, Path(tmp).resolve() / "adhoc" / "memory")

    def test_override_warns_on_cli_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            Host.open(tmp).create("a")
            env = {"AGENTS_HOST_DIR": tmp, "AGENTS_WORKSPACE": "a",
                   "AGENTS_MEMORY_PATH": "/tmp/extmem"}
            with warnings.catch_warnings(record=True) as seen:
                warnings.simplefilter("always")
                Workspace.from_env(env)
            self.assertTrue(
                any(w.category is UserWarning and "AGENTS_MEMORY_PATH" in str(w.message)
                    for w in seen),
                [str(w.message) for w in seen],
            )

    def test_canonical_override_does_not_warn(self):
        """An override equal to the resolved path is a pin, not an override."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Host.open(tmp).create("a")
            env = {
                "AGENTS_HOST_DIR": tmp,
                "AGENTS_WORKSPACE": "a",
                "AGENTS_MEMORY_DIR": str(ws.dir / "memory"),
                "AGENTS_TRACES_DIR": str(ws.dir / "traces"),
                "AGENTS_USERS_FILE": str(Path(tmp) / "users.json"),
            }
            with warnings.catch_warnings(record=True) as seen:
                warnings.simplefilter("always")
                Workspace.from_env(env)
            self.assertFalse(
                any("domain env overrides" in str(w.message) for w in seen),
                [str(w.message) for w in seen],
            )

    def test_env_strips_foreign_and_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            a = host.create("a")
            b = host.create("b")
            poison = {
                "AGENTS_HOST_DIR": str(Path(tmp) / "other-host"),
                "AGENTS_HOME": str(Path(tmp) / "other-home"),
                "AGENTS_WORKSPACE": "b",
                "AGENTS_WORKSPACE_ROOT": str(b.dir),
                "AGENTS_WORKSPACE_DIR": str(b.dir / "other-work"),
                "AGENTS_WORKSPACES_DIR": str(Path(tmp) / "other-ws"),
                "AGENTS_DIR": str(b.dir),
                "AGENTS_WORKDIR": str(b.dir / "wd"),
                "AGENTS_PERSONA": "someone",
                "AGENTS_MEMORY_PATH": str(b.memory),
                "AGENTS_MEMORY_DIR": str(b.memory),
                "AGENTS_RELAY_STATE": str(b.relay_state),
                "AGENTS_RELAY_DIR": str(b.relay_state),
                "AGENTS_IDENTITY_PATH": str(b.dir / "id.json"),
                "AGENTS_KEYS_DIR": str(b.dir / "keys"),
                "KEEP_ME": "yes",
            }
            child = a.env(poison)
            self.assertEqual(child["KEEP_ME"], "yes")
            self.assertEqual(child["AGENTS_HOST_DIR"], str(Path(tmp).resolve()))
            self.assertEqual(child["AGENTS_WORKSPACE"], "a")
            self.assertEqual(child["AGENTS_WORKDIR"], str(a.workdir))
            self.assertNotIn("AGENTS_PERSONA", child)
            for key in (
                "AGENTS_HOME", "AGENTS_WORKSPACE_ROOT", "AGENTS_WORKSPACE_DIR",
                "AGENTS_WORKSPACES_DIR", "AGENTS_DIR",
            ):
                self.assertNotIn(key, child, key)
            # every resolver-owned value points inside workspace a (or the
            # shared host), never inside workspace b
            b_str = str(b.dir)
            for key, value in child.items():
                if key.startswith("AGENTS_"):
                    self.assertFalse(
                        str(value).startswith(b_str),
                        f"{key} leaks into b: {value}",
                    )
            # new + alias names agree
            self.assertEqual(child["AGENTS_MEMORY_DIR"], str(a.memory))
            self.assertEqual(child["AGENTS_MEMORY_PATH"], str(a.memory))
            self.assertEqual(child["AGENTS_RELAY_DIR"], str(a.relay_state))
            self.assertEqual(child["AGENTS_RELAY_STATE"], str(a.relay_state))
            self.assertEqual(child["AGENTS_KEYS_DIR"], str(host.keys))
            self.assertEqual(child["AGENTS_USERS_FILE"], str(host.users_file()))
            self.assertEqual(child["AGENTS_IDENTITY_PATH"], str(host.users_file()))
            # persona + workdir params
            child2 = a.env(poison, persona="ops", workdir="/tmp/wd2")
            self.assertEqual(child2["AGENTS_PERSONA"], "ops")
            self.assertEqual(child2["AGENTS_WORKDIR"], str(Path("/tmp/wd2").resolve()))

    def test_users_file_override_is_test_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("a")
            ws = host.workspace("a")
            self.assertEqual(ws.identity, Path(tmp) / "users.json")
            env = {"AGENTS_USERS_FILE": "/tmp/u.json"}
            self.assertEqual(host.users_file(env), _p("/tmp/u.json"))


class TestEnv(unittest.TestCase):
    def test_env_sets_level_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            ws = host.create("a")
            env = ws.env({})
            self.assertEqual(env["AGENTS_HOST_DIR"], str(host.dir))
            self.assertEqual(env["AGENTS_WORKSPACE"], "a")
            self.assertEqual(env["AGENTS_WORKDIR"], str(ws.workdir))

    def test_env_strips_removed(self):
        ws = Host.open("/tmp/h").workspace("a")
        env = ws.env({"AGENTS_DIR": "/tmp/x"})
        self.assertNotIn("AGENTS_DIR", env)


class TestLifecycle(unittest.TestCase):
    def test_ensure_creates_domains(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Host.open(tmp).create("a")
            for name in ("memory", "traces", "personas", "work"):
                self.assertTrue(ws.domain(name).is_dir(), name)

    def test_init_writes_and_reloads_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            ws = host.create("a", label="personal")
            again = host.workspace("a")
            self.assertEqual(again.id, "a")
            self.assertEqual(again.label, "personal")
            self.assertEqual(again.default_persona, "default")

    def test_create_persona(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("a", persona="chief")
            ws = host.workspace("a")
            self.assertEqual(ws.default_persona, "chief")
            self.assertTrue((ws.personas / "chief" / "persona.json").is_file())

    def test_migrate_moves_host_domains(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            (host.dir / "memory").mkdir(parents=True)
            (host.dir / "memory" / "facts.md").write_text("x", encoding="utf-8")
            ws = host.workspace("b")  # not created: no domain dirs yet
            moves = ws.migrate()
            self.assertEqual(len(moves), 1)
            self.assertTrue((ws.dir / "memory" / "facts.md").is_file())

    def test_delete_archives_to_trash(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("a")
            out = host.delete("a")
            self.assertFalse((host.workspaces_dir / "a").exists())
            self.assertTrue((out / "workspace.json").is_file())
            self.assertEqual(out.parent.name, ".trash")
            self.assertEqual(host.list_workspaces(), [])

    def test_delete_hard_removes(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("a")
            host.delete("a", hard=True)
            self.assertFalse((host.workspaces_dir / "a").exists())
            self.assertFalse((host.workspaces_dir / ".trash").exists())

    def test_delete_refuses_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("a")
            env = {"AGENTS_WORKSPACE": "a"}
            with self.assertRaises(ValueError):
                host.delete("a", env=env)

    def test_delete_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            with self.assertRaises(ValueError):
                host.delete("nope")


class TestDeprecatedSurface(unittest.TestCase):
    def test_root_property(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ws = Host.open("/tmp/h").workspace("a")
            self.assertEqual(ws.root, ws.dir)

    def test_resolve_by_id_and_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                host = Host.open(tmp)
                host.create("demo", label="Demo WS")
                ws = Workspace.resolve("demo", env={"AGENTS_HOST_DIR": tmp})
                self.assertIsNotNone(ws)
                self.assertEqual(ws.id, "demo")
                self.assertEqual(Workspace.resolve("Demo WS", env={"AGENTS_HOST_DIR": tmp}).id, "demo")
                self.assertIsNone(Workspace.resolve("nope", env={"AGENTS_HOST_DIR": tmp}))


if __name__ == "__main__":
    unittest.main()


class TestDomainAliases(unittest.TestCase):
    def test_memory_dir_new_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host(dir=Path(tmp) / "host")
            host.create("w1")
            env = {"AGENTS_HOST_DIR": str(host.dir), "AGENTS_WORKSPACE": "w1",
                   "AGENTS_MEMORY_DIR": tmp + "/new", "AGENTS_MEMORY_PATH": tmp + "/old"}
            ws = Workspace.from_env(env)
            self.assertEqual(ws.memory, Path(tmp + "/new").resolve())

    def test_memory_path_alias_warns(self):
        import warnings
        with tempfile.TemporaryDirectory() as tmp:
            host = Host(dir=Path(tmp) / "host")
            host.create("w1")
            env = {"AGENTS_HOST_DIR": str(host.dir), "AGENTS_WORKSPACE": "w1",
                   "AGENTS_MEMORY_PATH": tmp + "/old"}
            with warnings.catch_warnings(record=True) as seen:
                warnings.simplefilter("always")
                ws = Workspace.from_env(env)
                self.assertEqual(ws.memory, Path(tmp + "/old").resolve())
            self.assertTrue(any("AGENTS_MEMORY_PATH is deprecated" in str(w.message) for w in seen))

    def test_read_alias_helper(self):
        import warnings
        from agents_workspace.compat import read_alias
        self.assertEqual(read_alias({"NEW": "a", "OLD": "b"}, "NEW", "OLD"), "a")
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            self.assertEqual(read_alias({"OLD": "b"}, "NEW", "OLD"), "b")
        self.assertTrue(any("deprecated" in str(w.message) for w in seen))
        self.assertEqual(read_alias({}, "NEW", "OLD"), "")
