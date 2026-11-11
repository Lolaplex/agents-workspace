"""C80: explicit host migration — planner, executor, lock, log, rollback."""
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from agents_workspace import Host
from agents_workspace.migrate import (
    already_migrated,
    execute_migration,
    plan_migration,
    rollback,
    run_cli,
)


def _snapshot(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and "logs" not in p.parts:
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _legacy_tree(host_dir: Path, home: Path) -> None:
    """Full legacy fixture: ws domains + ws files + host infra + externals."""
    for name in ("memory", "rules", "skills", "modules", "schedules",
                 "traces", "routine-runs", "personas"):
        d = host_dir / name
        d.mkdir(parents=True)
        (d / f"{name}-file.txt").write_text(name, encoding="utf-8")
    for name in ("calendar.json", "store.db", "workspace.json",
                 ".tombstones.json", "sync_config.json"):
        (host_dir / name).write_text("{}", encoding="utf-8")
    (host_dir / "identity.json").write_text('{"users":{}}', encoding="utf-8")
    (host_dir / "known-dids.jsonl").write_text("{}\n", encoding="utf-8")
    (host_dir / "secrets.json").write_text("{}", encoding="utf-8")
    (host_dir / "mcp.json").write_text("{}", encoding="utf-8")
    (host_dir / "unknown-thing.md").write_text("data", encoding="utf-8")
    keys = host_dir / "keys"
    keys.mkdir()
    (keys / "agent.ed25519").write_text("key", encoding="utf-8")

    home.mkdir(parents=True, exist_ok=True)
    (home / ".agents-relay" / "inbox").mkdir(parents=True)
    (home / ".agents-workspaces" / "proj-a").mkdir(parents=True)
    (home / ".agents-workspaces" / "proj-a" / "memory").mkdir()
    (home / ".client").mkdir()
    (home / ".client" / "cfg.json").write_text("{}", encoding="utf-8")
    klanker = home / ".klanker"
    klanker.mkdir()
    (klanker / "daemon.pid").write_text("999999999", encoding="utf-8")


class TestMigration(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.host_dir = self.tmp / "host"
        self.home = self.tmp / "home"
        self.host = Host(dir=self.host_dir)

    def tearDown(self):
        self._tmp.cleanup()

    def test_full_legacy_fixture_moves_everything(self):
        _legacy_tree(self.host_dir, self.home)
        moves = plan_migration(self.host, home=self.home)
        log = execute_migration(moves, self.host)
        ws = self.host_dir / "workspaces" / "default"

        for name in ("memory", "rules", "skills", "modules", "schedules",
                     "traces", "routine-runs", "personas"):
            self.assertTrue((ws / name / f"{name}-file.txt").is_file(), name)
            self.assertFalse((self.host_dir / name).exists(), name)
        for name in ("calendar.json", "store.db", "workspace.json",
                     ".tombstones.json", "sync_config.json"):
            self.assertTrue((ws / name).is_file(), name)
        # catch-all
        self.assertTrue((ws / "unknown-thing.md").is_file())
        # identity -> host users.json; known-dids -> keys/
        self.assertTrue((self.host_dir / "users.json").is_file())
        self.assertFalse((self.host_dir / "identity.json").exists())
        self.assertTrue((self.host_dir / "keys" / "known-dids.jsonl").is_file())
        # host files stayed put (no-op)
        self.assertTrue((self.host_dir / "secrets.json").is_file())
        self.assertTrue((self.host_dir / "mcp.json").is_file())
        self.assertTrue((self.host_dir / "keys" / "agent.ed25519").is_file())
        # externals
        # ~/.agents-relay landed in the default ws relay domain
        self.assertTrue((ws / "relay" / "inbox").is_dir())
        self.assertFalse((self.home / ".agents-relay").exists())
        self.assertTrue((self.host_dir / "workspaces" / "proj-a" / "memory").is_dir())
        self.assertTrue((self.host_dir / "clients" / "cfg.json").is_file())
        self.assertFalse((self.home / ".klanker" / "daemon.pid").exists())
        # log written
        self.assertTrue(log.is_file())
        data = json.loads(log.read_text(encoding="utf-8"))
        self.assertGreater(len(data["moves"]), 0)

    def test_conflict_aborts_with_zero_movement(self):
        _legacy_tree(self.host_dir, self.home)
        blocker = self.host_dir / "workspaces" / "default" / "memory"
        blocker.mkdir(parents=True)
        (blocker / "keep.txt").write_text("keep", encoding="utf-8")
        before = _snapshot(self.host_dir)
        moves = plan_migration(self.host, home=self.home)
        with self.assertRaises(RuntimeError):
            execute_migration(moves, self.host)
        self.assertEqual(before, _snapshot(self.host_dir))
        self.assertTrue((self.host_dir / "memory").is_dir())
        self.assertTrue((blocker / "keep.txt").is_file())

    def test_rollback_restores_byte_identical(self):
        _legacy_tree(self.host_dir, self.home)
        before = _snapshot(self.host_dir)
        moves = plan_migration(self.host, home=self.home)
        log = execute_migration(moves, self.host)
        restored = rollback(log)
        self.assertGreater(restored, 0)
        self.assertEqual(before, _snapshot(self.host_dir))
        self.assertFalse((self.host_dir / "users.json").exists())
        self.assertTrue((self.host_dir / "identity.json").is_file())

    def test_relay_collision_aborts(self):
        # host relay/ + ~/.agents-relay both target <ws>/relay -> abort
        (self.host_dir / "relay").mkdir(parents=True)
        (self.home / ".agents-relay").mkdir(parents=True)
        moves = plan_migration(self.host, home=self.home)
        with self.assertRaises(RuntimeError):
            execute_migration(moves, self.host)
        self.assertTrue((self.host_dir / "relay").is_dir())
        self.assertTrue((self.home / ".agents-relay").is_dir())

    def test_second_run_reports_already_migrated(self):
        _legacy_tree(self.host_dir, self.home)
        execute_migration(plan_migration(self.host, home=self.home), self.host)
        self.assertTrue(already_migrated(self.host))
        self.assertEqual(run_cli(self.host, home=self.home), 0)

    def test_dry_run_moves_nothing(self):
        _legacy_tree(self.host_dir, self.home)
        before = _snapshot(self.host_dir)
        moves = plan_migration(self.host, home=self.home)
        execute_migration(moves, self.host, dry_run=True)
        self.assertEqual(before, _snapshot(self.host_dir))
        self.assertFalse((self.host_dir / "workspaces").exists())

    def test_lock_blocks_concurrent_run(self):
        _legacy_tree(self.host_dir, self.home)
        self.host.run.mkdir(parents=True)
        (self.host.run / "migrate.lock").write_text("x", encoding="utf-8")
        moves = plan_migration(self.host, home=self.home)
        with self.assertRaises(RuntimeError):
            execute_migration(moves, self.host)
        self.assertTrue((self.host_dir / "memory").is_dir())

    def test_ws_migrate_wrapper(self):
        (self.host_dir / "memory").mkdir(parents=True)
        (self.host_dir / "memory" / "f.md").write_text("x", encoding="utf-8")
        ws = self.host.workspace("b")
        moves = ws.migrate()
        self.assertEqual(len(moves), 1)
        self.assertTrue((ws.dir / "memory" / "f.md").is_file())

    def test_nothing_to_migrate(self):
        self.host.create("a")
        self.assertEqual(plan_migration(self.host, home=self.home), [])


if __name__ == "__main__":
    unittest.main()
