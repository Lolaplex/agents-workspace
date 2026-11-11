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
            self.assertIn(str(ws_dir), out)

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


if __name__ == "__main__":
    unittest.main()
