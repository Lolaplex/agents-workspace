"""CLI tests."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agents_workspace.cli import main


def _run(argv: list[str], env: dict[str, str]) -> tuple[int, str]:
    buf = io.StringIO()
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

    def test_init_creates_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _run(["init", "--root", tmp, "--label", "personal"], {})
            self.assertEqual(code, 0)
            data = json.loads(out)
            self.assertEqual(data["label"], "personal")
            self.assertTrue((Path(tmp) / "workspace.json").is_file())

    def test_migrate_reports_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            from agents_workspace import Host

            Host.open(tmp).create("a")
            code, out = _run(
                ["migrate"], {"AGENTS_HOST_DIR": tmp, "AGENTS_WORKSPACE": "a"}
            )
            self.assertEqual(code, 0)
            self.assertIn("nothing to migrate", out)


if __name__ == "__main__":
    unittest.main()
