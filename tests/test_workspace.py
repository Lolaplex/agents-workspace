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
