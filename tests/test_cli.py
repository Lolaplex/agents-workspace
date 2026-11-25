"""CLI tests."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import warnings
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agents_workspace.cli import main


def _run(argv: list[str], env: dict[str, str]) -> tuple[int, str]:
    buf = io.StringIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with patch.dict(os.environ, env, clear=False), redirect_stdout(buf):
            code = main(argv)
    return code, buf.getvalue()


class TestCli(unittest.TestCase):
    def test_path_prints_domain(self):
        code, out = _run(["path", "traces"], {"AGENTS_WORKSPACE_ROOT": "/tmp/ws"})
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), str(Path("/tmp/ws/traces").resolve()))

    def test_info_json_has_all_domains(self):
        code, out = _run(["info", "--json"], {"AGENTS_WORKSPACE_ROOT": "/tmp/ws"})
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["root"], str(Path("/tmp/ws").resolve()))
        self.assertIn("memory", data["domains"])
        self.assertIn("personas", data["domains"])

    def test_create_and_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AGENTS_HOST_DIR": tmp}
            code, out = _run(["create", "a", "--label", "personal"], env)
            self.assertEqual(code, 0)
            data = json.loads(out)
            self.assertEqual(data["label"], "personal")
            self.assertEqual(data["id"], "a")
            ws_dir = Path(tmp) / "workspaces" / "a"
            self.assertTrue((ws_dir / "workspace.json").is_file())
            self.assertTrue(
                (ws_dir / "personas" / "default" / "persona.json").is_file()
            )
            code, out = _run(["list"], env)
            self.assertEqual(code, 0)
            self.assertIn("a", out)
            # CLI prints the canonical on-disk casing; tempfile keeps the
            # env-var casing (Temp vs TEMP) — same directory on Windows.
            self.assertIn(os.path.normcase(str(ws_dir)), os.path.normcase(out))

    def test_show_and_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AGENTS_HOST_DIR": tmp}
            _run(["create", "a"], env)
            code, out = _run(["show", "a", "--json"], env)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["id"], "a")
            code, out = _run(["env", "a"], env)
            self.assertEqual(code, 0)
            lines = dict(
                line.split("=", 1) for line in out.strip().splitlines()
            )
            self.assertEqual(lines["AGENTS_WORKSPACE"], "a")
            self.assertEqual(lines["AGENTS_HOST_DIR"], str(Path(tmp).resolve()))
            self.assertEqual(
                lines["AGENTS_MEMORY_DIR"],
                str(Path(tmp).resolve() / "workspaces" / "a" / "memory"),
            )
            self.assertNotIn("AGENTS_HOME", lines)
            self.assertNotIn("AGENTS_WORKSPACE_ROOT", lines)

    def test_show_unknown_id_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _ = _run(["show", "nope"], {"AGENTS_HOST_DIR": tmp})
            self.assertEqual(code, 2)

    def test_init_refuses_root_outside_workspaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _ = _run(
                ["init", "--root", tmp], {"AGENTS_HOST_DIR": tmp}
            )
            self.assertEqual(code, 2)

    def test_init_inside_workspaces_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspaces" / "legacy"
            code, out = _run(
                ["init", "--root", str(root), "--label", "old"],
                {"AGENTS_HOST_DIR": tmp},
            )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["label"], "old")

    def test_migrate_reports_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            from agents_workspace import Host

            Host.open(tmp).create("a")
            code, out = _run(
                ["migrate"], {"AGENTS_HOST_DIR": tmp, "AGENTS_WORKSPACE": "a"}
            )
            self.assertEqual(code, 0)
            self.assertIn("already migrated", out)

    def test_secrets_set_list_unset_and_doctor(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"AGENTS_HOST_DIR": tmp, "AGENTS_KEYRING": "0"}
            _run(["create", "a"], env)
            code, out = _run(
                ["secrets", "set", "OPENAI_API_KEY", "--scope", "workspace",
                 "--workspace", "a", "--value", "secret-val-123"],
                env,
            )
            self.assertEqual(code, 0, out)
            self.assertNotIn("secret-val-123", out)
            code, out = _run(["secrets", "list", "--workspace", "a"], env)
            self.assertEqual(code, 0)
            self.assertEqual(out.strip(), "OPENAI_API_KEY")
            code, out = _run(["doctor", "--json"], env)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["status"], "ok")
            code, out = _run(
                ["secrets", "unset", "OPENAI_API_KEY", "--scope", "workspace", "--workspace", "a"],
                env,
            )
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(out)["removed"])

    def test_gc_prunes_only_old_archive_and_conflicts(self):
        import os
        import time

        with tempfile.TemporaryDirectory() as tmp:
            env = {"AGENTS_HOST_DIR": tmp}
            _run(["create", "a"], env)
            host_p = Path(tmp)
            old_arch = host_p / "archive" / "old-stamp"
            old_unclass = host_p / "archive" / "unclassified" / "old-junk.txt"
            fresh_conf = host_p / "conflicts" / "fresh"
            old_arch.mkdir(parents=True)
            (old_arch / "x.txt").write_text("x", encoding="utf-8")
            old_unclass.parent.mkdir(parents=True)
            old_unclass.write_text("junk", encoding="utf-8")
            fresh_conf.mkdir(parents=True)
            (fresh_conf / "y.txt").write_text("y", encoding="utf-8")

            stale_ts = time.time() - (45 * 86400)
            os.utime(old_arch, (stale_ts, stale_ts))
            os.utime(old_unclass, (stale_ts, stale_ts))

            code, out = _run(["gc", "--older-than", "30d", "--dry-run", "--json"], env)
            self.assertEqual(code, 0, out)
            dry = json.loads(out)
            self.assertTrue(dry["dry_run"])
            self.assertEqual(dry["count"], 2)
            self.assertTrue(old_arch.exists())
            self.assertTrue(old_unclass.exists())

            code, out = _run(["gc", "--older-than", "30d", "--json"], env)
            self.assertEqual(code, 0, out)
            real = json.loads(out)
            self.assertFalse(real["dry_run"])
            self.assertEqual(real["count"], 2)
            self.assertFalse(old_arch.exists())
            self.assertFalse(old_unclass.exists())
            self.assertTrue(fresh_conf.exists())
            self.assertTrue((host_p / "workspaces" / "a" / "workspace.json").is_file())


if __name__ == "__main__":
    unittest.main()
