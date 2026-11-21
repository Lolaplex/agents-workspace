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
    (host_dir / "secrets.json").write_text('{"OPENAI_API_KEY": "k1"}', encoding="utf-8")
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
        # catch-all moves unclassified files to archive/unclassified (never ws)
        self.assertTrue((self.host_dir / "archive" / "unclassified" / "unknown-thing.md").is_file())
        self.assertFalse((ws / "unknown-thing.md").exists())
        # identity -> host users.json; known-dids -> keys/; secrets -> secrets/host.json
        self.assertTrue((self.host_dir / "users.json").is_file())
        self.assertFalse((self.host_dir / "identity.json").exists())
        self.assertTrue((self.host_dir / "keys" / "known-dids.jsonl").is_file())
        self.assertTrue((self.host_dir / "secrets" / "host.json").is_file())
        self.assertFalse((self.host_dir / "secrets.json").exists())
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

    def test_conflict_merges_into_existing_ws_dir(self):
        _legacy_tree(self.host_dir, self.home)
        blocker = self.host_dir / "workspaces" / "default" / "memory"
        blocker.mkdir(parents=True)
        (blocker / "keep.txt").write_text("keep", encoding="utf-8")
        moves = plan_migration(self.host, home=self.home)
        execute_migration(moves, self.host)
        # free entries merge in, pre-existing ws content stays
        self.assertTrue((blocker / "keep.txt").is_file())
        self.assertTrue((blocker / "memory-file.txt").is_file())
        self.assertFalse((self.host_dir / "memory").exists())

    def test_conflicting_file_is_quarantined(self):
        _legacy_tree(self.host_dir, self.home)
        blocker = self.host_dir / "workspaces" / "default"
        blocker.mkdir(parents=True)
        (blocker / "calendar.json").write_text('{"new":1}', encoding="utf-8")
        moves = plan_migration(self.host, home=self.home)
        execute_migration(moves, self.host)
        self.assertEqual(
            (blocker / "calendar.json").read_text("utf-8"), '{"new":1}')
        quarantined = list(
            (self.host_dir / "conflicts").rglob("calendar.json"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(
            quarantined[0].read_text("utf-8"), "{}")

    def test_identical_file_is_dropped_not_quarantined(self):
        _legacy_tree(self.host_dir, self.home)
        blocker = self.host_dir / "workspaces" / "default"
        blocker.mkdir(parents=True)
        (blocker / "calendar.json").write_text("{}", encoding="utf-8")
        moves = plan_migration(self.host, home=self.home)
        execute_migration(moves, self.host)
        self.assertFalse((self.host_dir / "calendar.json").exists())
        self.assertFalse((self.host_dir / "conflicts").exists())

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

    def test_relay_collision_merges(self):
        # host relay/ + ~/.agents-relay both target <ws>/relay -> merge
        (self.host_dir / "relay").mkdir(parents=True)
        (self.host_dir / "relay" / "a.txt").write_text("a", encoding="utf-8")
        (self.home / ".agents-relay").mkdir(parents=True)
        (self.home / ".agents-relay" / "b.txt").write_text("b", encoding="utf-8")
        moves = plan_migration(self.host, home=self.home)
        execute_migration(moves, self.host)
        ws_relay = self.host_dir / "workspaces" / "default" / "relay"
        self.assertTrue((ws_relay / "a.txt").is_file())
        self.assertTrue((ws_relay / "b.txt").is_file())
        self.assertFalse((self.host_dir / "relay").exists())
        self.assertFalse((self.home / ".agents-relay").exists())

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

    def test_browser_profiles_stay_on_host(self):
        # legacy top-level browser* dirs are Chromium profiles, not the
        # <ws>/browser artifacts domain
        for name in ("browser", "browser-edge", "browser_test",
                     "chromium-moodle"):
            d = self.host_dir / name
            d.mkdir(parents=True)
            (d / "profile.bin").write_text(name, encoding="utf-8")
        moves = plan_migration(self.host, home=self.home)
        bad = [m for m in moves if m.kind == "ws"
               and ("browser" in m.src.name or "chromium" in m.src.name)]
        self.assertEqual(bad, [])
        execute_migration(moves, self.host)
        for name in ("browser", "browser-edge", "browser_test",
                     "chromium-moodle"):
            self.assertTrue((self.host_dir / name / "profile.bin").is_file(), name)
            self.assertFalse(
                (self.host_dir / "workspaces" / "default" / name).exists(), name)

    def test_identity_merges_into_existing_users(self):
        self.host_dir.mkdir(parents=True)
        (self.host_dir / "identity.json").write_text(
            json.dumps({"users": {"u_a": {"id": "u_a", "aliases": ["a"]},
                                  "u_b": {"id": "u_b", "aliases": ["b"]}}}),
            encoding="utf-8")
        (self.host_dir / "users.json").write_text(
            json.dumps({"users": {"u_keep": {"did": "did:key:z1"}}}),
            encoding="utf-8")
        moves = plan_migration(self.host, home=self.home)
        merge = [m for m in moves if m.kind == "merge-users"]
        self.assertEqual(len(merge), 1)
        execute_migration(moves, self.host)
        users = json.loads((self.host_dir / "users.json").read_text("utf-8"))["users"]
        self.assertEqual(set(users), {"u_keep", "u_a", "u_b"})
        self.assertEqual(users["u_keep"]["did"], "did:key:z1")
        self.assertFalse((self.host_dir / "identity.json").exists())

    def test_merge_users_rollback_restores_both_files(self):
        self.host_dir.mkdir(parents=True)
        ident_body = json.dumps({"users": {"u_a": {"id": "u_a"}}})
        users_body = json.dumps({"users": {"u_keep": {"did": "did:key:z1"}}})
        (self.host_dir / "identity.json").write_text(ident_body, encoding="utf-8")
        (self.host_dir / "users.json").write_text(users_body, encoding="utf-8")
        moves = plan_migration(self.host, home=self.home)
        log = execute_migration(moves, self.host)
        rollback(log)
        self.assertEqual(
            (self.host_dir / "identity.json").read_text("utf-8"), ident_body)
        self.assertEqual(
            (self.host_dir / "users.json").read_text("utf-8"), users_body)

    def test_host_file_and_dir_remaps(self):
        self.host_dir.mkdir(parents=True)
        (self.host_dir / "mcp-tools-cache.json").write_text("{}", encoding="utf-8")
        (self.host_dir / "klanker_prompt.txt").write_text("p", encoding="utf-8")
        (self.host_dir / "repl").mkdir(parents=True)
        (self.host_dir / "repl" / "prefs.json").write_text("{}", encoding="utf-8")
        moves = plan_migration(self.host, home=self.home)
        execute_migration(moves, self.host)
        self.assertTrue(
            (self.host_dir / "cache" / "mcp-tools-cache.json").is_file())
        self.assertTrue(
            (self.host_dir / "run" / "klanker_prompt_default.txt").is_file())
        self.assertTrue(
            (self.host_dir / "clients" / "repl" / "prefs.json").is_file())
        self.assertFalse((self.host_dir / "klanker_prompt.txt").exists())
        self.assertFalse((self.host_dir / "repl").exists())

    def test_harness_content_merges_into_ws_content(self):
        self.host_dir.mkdir(parents=True)
        hc = self.host_dir / "harness-content"
        (hc / "ab").mkdir(parents=True)
        (hc / "ab" / "body.txt").write_text("clip", encoding="utf-8")
        (hc / "same.txt").write_text("x", encoding="utf-8")
        # pre-existing identical content in the target domain -> deleted
        c = self.host_dir / "content"
        (c).mkdir()
        (c / "same.txt").write_text("x", encoding="utf-8")
        moves = plan_migration(self.host, home=self.home)
        execute_migration(moves, self.host)
        ws = self.host_dir / "workspaces" / "default" / "content"
        self.assertTrue((ws / "ab" / "body.txt").is_file())
        self.assertTrue((ws / "same.txt").is_file())
        self.assertFalse((self.host_dir / "harness-content").exists())

    def test_bak_dirs_archive_and_empty_workspace_dir_dies(self):
        self.host_dir.mkdir(parents=True)
        (self.host_dir / "memory.bak-20990101T000000Z").mkdir(parents=True)
        (self.host_dir / "memory.bak-20990101T000000Z" / "f.md").write_text(
            "x", encoding="utf-8")
        (self.host_dir / "workspace").mkdir()
        moves = plan_migration(self.host, home=self.home)
        execute_migration(moves, self.host)
        self.assertTrue(
            (self.host_dir / "archive" / "memory.bak-20990101T000000Z"
             / "f.md").is_file())
        self.assertFalse(
            (self.host_dir / "memory.bak-20990101T000000Z").exists())
        self.assertFalse((self.host_dir / "workspace").exists())

    def test_klanker_instance_payload_merges_and_archives(self):
        self.host_dir.mkdir(parents=True)
        klanker = self.home / ".klanker"
        inst = klanker / "instances" / "default"
        ws_dir = inst / "workspaces" / "default"
        sessions = ws_dir / "personas" / "default" / "sessions"
        sessions.mkdir(parents=True)
        (sessions / "ses_1.json").write_text("{}", encoding="utf-8")
        (ws_dir / "workspace.json").write_text("{}", encoding="utf-8")
        (inst / "instance.json").write_text(
            json.dumps({"id": "default", "secret": "strip-me"}), encoding="utf-8"
        )
        (inst / "users.json").write_text("{}", encoding="utf-8")
        (inst / "daemon.pid").write_text("999999999", encoding="utf-8")
        (klanker / "relay-secret").write_text("pin-tok\n", encoding="utf-8")
        (klanker / "settings.json").write_text('{"theme":"dark"}', encoding="utf-8")
        (klanker / "serve.json").write_text('{"pid": 999999999}', encoding="utf-8")
        (klanker / "serve.log").write_text("boot\n", encoding="utf-8")
        moves = plan_migration(self.host, home=self.home)
        execute_migration(moves, self.host)
        ws = self.host_dir / "workspaces" / "default"
        self.assertTrue(
            (ws / "personas" / "default" / "sessions" / "ses_1.json").is_file())
        self.assertTrue((ws / "workspace.json").is_file())
        # desktop registry, settings, serve state, and relay token relocated cleanly
        self.assertFalse((inst / "instance.json").exists())
        desktop_reg = self.host_dir / "clients" / "desktop" / "instances.json"
        self.assertTrue(desktop_reg.is_file())
        reg_rows = json.loads(desktop_reg.read_text("utf-8"))
        self.assertEqual(reg_rows[0]["id"], "default")
        self.assertNotIn("secret", reg_rows[0])
        self.assertTrue((self.host_dir / "clients" / "desktop" / "settings.json").is_file())
        self.assertTrue((self.host_dir / "run" / "serve.json").is_file())
        self.assertTrue((self.host_dir / "logs" / "serve.log").is_file())
        vault = json.loads((self.host_dir / "secrets" / "host.json").read_text("utf-8"))
        self.assertEqual(vault["RELAY_WEB_TOKEN"], "pin-tok")
        self.assertTrue(
            (self.host_dir / "archive" / "klanker" / "default"
             / "users.json").is_file())
        self.assertFalse((inst / "daemon.pid").exists())
        self.assertFalse((inst / "workspaces").exists())

    def test_ws_secrets_and_relay_files_relocate_and_rollback(self):
        ws = self.host.create("default")
        ws.secrets.write_text(
            json.dumps({"OPENAI_API_KEY": "ws-key", "RELAY_SECRET": "poison"}),
            encoding="utf-8",
        )
        ws.relay_state.mkdir(parents=True, exist_ok=True)
        (ws.relay_state / "vapid.json").write_text('{"private":"p"}', encoding="utf-8")
        (ws.relay_state / "push.json").write_text('{"subs":[]}', encoding="utf-8")
        (ws.relay_state / "chat_prefs.json").write_text('{"1":{"a":1}}', encoding="utf-8")
        (ws.relay_state / "remotes.json").write_text(
            json.dumps({"remotes": {"box1": {"base_url": "https://b1", "label": "B1", "secret": "tok-b1"}}}),
            encoding="utf-8",
        )
        before = _snapshot(self.host_dir)
        self.assertFalse(already_migrated(self.host))
        log = execute_migration(plan_migration(self.host, home=self.home), self.host)
        self.assertTrue(already_migrated(self.host))
        self.assertFalse(ws.secrets.exists())
        ws_vault = json.loads((self.host_dir / "secrets" / "ws-default.json").read_text("utf-8"))
        self.assertEqual(ws_vault, {"OPENAI_API_KEY": "ws-key"})
        host_vault = json.loads((self.host_dir / "secrets" / "host.json").read_text("utf-8"))
        self.assertEqual(host_vault["REMOTE_BOX1_TOKEN"], "tok-b1")
        remotes_meta = json.loads((self.host_dir / "clients" / "remotes.json").read_text("utf-8"))
        self.assertEqual(remotes_meta["remotes"]["box1"], {"base_url": "https://b1", "label": "B1"})
        self.assertTrue((self.host_dir / "keys" / "vapid.json").is_file())
        self.assertTrue((self.host_dir / "secrets" / "push.json").is_file())
        self.assertTrue((self.host_dir / "clients" / "telegram.json").is_file())
        rollback(log)
        self.assertEqual(before, _snapshot(self.host_dir))

    def test_ws_secrets_uses_meta_id_and_stale_pid_lock_recovers(self):
        import json as _json

        ws = self.host.create("dirname")
        meta = _json.loads(ws.workspace_file.read_text(encoding="utf-8"))
        meta["id"] = "metaid"
        ws.workspace_file.write_text(_json.dumps(meta), encoding="utf-8")
        ws.secrets.write_text(_json.dumps({"K": "v"}), encoding="utf-8")
        (self.host_dir / ".env").write_text("SECRET=1\n", encoding="utf-8")
        self.host.run.mkdir(parents=True, exist_ok=True)
        (self.host.run / "migrate.lock").write_text("999999999\n", encoding="utf-8")
        moves = plan_migration(self.host, home=self.home)
        execute_migration(moves, self.host)
        self.assertTrue((self.host_dir / "secrets" / "ws-metaid.json").is_file())
        self.assertTrue((self.host_dir / "archive" / "unclassified" / ".env").is_file())
        self.assertFalse((ws.dir / ".env").exists())

    def test_stale_regenerable_cache_is_deleted(self):
        self.host_dir.mkdir(parents=True)
        (self.host_dir / "mcp-tools-cache.json").write_text(
            '{"old":true}', encoding="utf-8")
        cache = self.host_dir / "cache"
        cache.mkdir()
        (cache / "mcp-tools-cache.json").write_text(
            '{"new":true}', encoding="utf-8")
        moves = plan_migration(self.host, home=self.home)
        execute_migration(moves, self.host)
        self.assertFalse((self.host_dir / "mcp-tools-cache.json").exists())
        self.assertEqual(
            json.loads((cache / "mcp-tools-cache.json").read_text("utf-8")),
            {"new": True})


if __name__ == "__main__":
    unittest.main()
