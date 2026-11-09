"""Workspace resolution tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents_workspace import DIR_DOMAINS, Workspace


def _p(raw: str) -> Path:
    return Path(raw).expanduser().resolve()


class TestRoot(unittest.TestCase):
    def test_default_root_is_legacy(self):
        with patch("agents_workspace.workspace.legacy_root", return_value=_p("/tmp/legacy")):
            ws = Workspace.from_env({})
        self.assertEqual(ws.root, _p("/tmp/legacy"))
        self.assertFalse(ws.explicit_root)

    def test_new_root_wins_over_legacy(self):
        ws = Workspace.from_env(
            {"AGENTS_WORKSPACE_ROOT": "/tmp/ws", "AGENTS_HOME": "/tmp/home"}
        )
        self.assertEqual(ws.root, _p("/tmp/ws"))
        self.assertTrue(ws.explicit_root)

    def test_legacy_root_is_read(self):
        ws = Workspace.from_env({"AGENTS_HOME": "/tmp/home"})
        self.assertEqual(ws.root, _p("/tmp/home"))
        self.assertTrue(ws.explicit_root)

    def test_workdir_defaults_to_root_workspace(self):
        ws = Workspace.from_env({"AGENTS_WORKSPACE_ROOT": "/tmp/ws"})
        self.assertEqual(ws.workdir, _p("/tmp/ws/workspace"))

    def test_workdir_new_env_wins(self):
        ws = Workspace.from_env(
            {
                "AGENTS_WORKSPACE_ROOT": "/tmp/ws",
                "AGENTS_WORKDIR": "/tmp/new",
                "AGENTS_WORKSPACE_DIR": "/tmp/old",
            }
        )
        self.assertEqual(ws.workdir, _p("/tmp/new"))

    def test_workdir_legacy_env_is_read(self):
        ws = Workspace.from_env(
            {"AGENTS_WORKSPACE_ROOT": "/tmp/ws", "AGENTS_WORKSPACE_DIR": "/tmp/old"}
        )
        self.assertEqual(ws.workdir, _p("/tmp/old"))


class TestDomains(unittest.TestCase):
    def test_defaults_follow_root(self):
        ws = Workspace.from_env({"AGENTS_WORKSPACE_ROOT": "/tmp/ws"})
        for name in DIR_DOMAINS:
            self.assertEqual(ws.domain(name), _p(f"/tmp/ws/{name}"), name)

    def test_domain_env_override(self):
        ws = Workspace.from_env(
            {
                "AGENTS_WORKSPACE_ROOT": "/tmp/ws",
                "AGENTS_TRACES_DIR": "/tmp/traces",
                "AGENTS_MEMORY_PATH": "/tmp/memory",
                "AGENTS_SCHEDULES_DIR": "/tmp/schedules",
                "AGENTS_RELAY_STATE": "/tmp/relay",
                "AGENTS_PERSONAS_DIR": "/tmp/personas",
            }
        )
        self.assertEqual(ws.traces, _p("/tmp/traces"))
        self.assertEqual(ws.memory, _p("/tmp/memory"))
        self.assertEqual(ws.schedules, _p("/tmp/schedules"))
        self.assertEqual(ws.relay_state, _p("/tmp/relay"))
        self.assertEqual(ws.personas, _p("/tmp/personas"))

    def test_identity_override(self):
        ws = Workspace.from_env(
            {"AGENTS_WORKSPACE_ROOT": "/tmp/ws", "AGENTS_IDENTITY_PATH": "/tmp/id.json"}
        )
        self.assertEqual(ws.identity, _p("/tmp/id.json"))

    def test_identity_default(self):
        ws = Workspace.from_env({"AGENTS_WORKSPACE_ROOT": "/tmp/ws"})
        self.assertEqual(ws.identity, _p("/tmp/ws/identity.json"))

    def test_claude_default_without_explicit_root(self):
        with patch("agents_workspace.workspace.legacy_root", return_value=_p("/tmp/legacy")):
            ws = Workspace.from_env({})
        self.assertEqual(ws.claude, Path.home() / ".claude")

    def test_claude_follows_explicit_root(self):
        ws = Workspace.from_env({"AGENTS_WORKSPACE_ROOT": "/tmp/ws"})
        self.assertEqual(ws.claude, _p("/tmp/ws/claude"))

    def test_unknown_domain_raises(self):
        ws = Workspace.from_env({"AGENTS_WORKSPACE_ROOT": "/tmp/ws"})
        with self.assertRaises(KeyError):
            ws.domain("nope")


class TestRegistry(unittest.TestCase):
    def test_env_sets_every_domain(self):
        ws = Workspace.from_env({"AGENTS_WORKSPACE_ROOT": "/tmp/ws"})
        env = ws.env({})
        self.assertEqual(env["AGENTS_WORKSPACE_ROOT"], str(ws.root))
        self.assertEqual(env["AGENTS_MEMORY_PATH"], str(ws.memory))
        self.assertEqual(env["AGENTS_TRACES_DIR"], str(ws.traces))
        self.assertEqual(env["AGENTS_PERSONAS_DIR"], str(ws.personas))

    def test_create_and_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"AGENTS_WORKSPACES_DIR": tmp}, clear=False):
                ws = Workspace.create("Personal Notes")
                self.assertTrue((ws.root / "workspace.json").is_file())
                self.assertEqual(ws.label, "Personal Notes")
                rows = Workspace.list_all()
                self.assertTrue(any(row.root == ws.root for row in rows))

    def test_resolve_by_label_and_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"AGENTS_WORKSPACES_DIR": tmp}, clear=False):
                ws = Workspace.create("demo")
                self.assertEqual(Workspace.resolve("demo").root, ws.root)
                self.assertEqual(Workspace.resolve(str(ws.root)).root, ws.root)
                self.assertIsNone(Workspace.resolve("nope"))


class TestLifecycle(unittest.TestCase):
    def test_ensure_creates_every_dir_domain(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace.open(tmp).ensure()
            for name in DIR_DOMAINS:
                self.assertTrue(ws.domain(name).is_dir(), name)

    def test_init_writes_and_reloads_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace.open(tmp).init(label="personal")
            self.assertTrue(ws.workspace_file.is_file())
            self.assertTrue(ws.id.startswith("ws_"))
            again = Workspace.open(tmp)
            self.assertEqual(again.id, ws.id)
            self.assertEqual(again.label, "personal")
            self.assertEqual(again.default_persona, "default")

    def test_init_keeps_existing_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Workspace.open(tmp).init(label="one")
            second = Workspace.open(tmp).init(label="two")
            self.assertEqual(first.id, second.id)
            self.assertEqual(second.label, "two")

    def test_migrate_moves_legacy_domains(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "legacy"
            (legacy / "memory").mkdir(parents=True)
            (legacy / "memory" / "facts.md").write_text("x", encoding="utf-8")
            target = Path(tmp) / "ws"
            with patch("agents_workspace.workspace.legacy_root", return_value=legacy.resolve()):
                moves = Workspace.open(target).migrate()
            self.assertEqual(len(moves), 1)
            self.assertTrue((target / "memory" / "facts.md").is_file())
            self.assertFalse((legacy / "memory").exists())

    def test_migrate_dry_run_keeps_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "legacy"
            (legacy / "traces").mkdir(parents=True)
            target = Path(tmp) / "ws"
            with patch("agents_workspace.workspace.legacy_root", return_value=legacy.resolve()):
                moves = Workspace.open(target).migrate(dry_run=True)
            self.assertEqual(len(moves), 1)
            self.assertTrue((legacy / "traces").is_dir())
            self.assertFalse((target / "traces").exists())

    def test_migrate_skips_existing_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "legacy"
            (legacy / "memory").mkdir(parents=True)
            target = Path(tmp) / "ws"
            (target / "memory").mkdir(parents=True)
            with patch("agents_workspace.workspace.legacy_root", return_value=legacy.resolve()):
                moves = Workspace.open(target).migrate()
            self.assertEqual(moves, [])

    def test_migrate_noop_at_legacy_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "legacy"
            (legacy / "memory").mkdir(parents=True)
            with patch("agents_workspace.workspace.legacy_root", return_value=legacy.resolve()):
                moves = Workspace.open(legacy).migrate()
            self.assertEqual(moves, [])


if __name__ == "__main__":
    unittest.main()
