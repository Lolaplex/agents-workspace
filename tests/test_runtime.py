"""Isolation dispatch, container path/env rewriting, passthrough allowlist."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents_workspace import Host
from agents_workspace import runtime


def _ws(tmp: str, ws_id: str = "qa", isolation: str = ""):
    host = Host(dir=Path(tmp))
    ws = host.create(ws_id)
    if isolation:
        from dataclasses import replace

        ws = replace(ws, isolation=isolation)
    return ws


class TestIsolationOf(unittest.TestCase):
    def test_default_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _ws(tmp)
            self.assertEqual(runtime.isolation_of(ws), "local")

    def test_env_default_container(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _ws(tmp)
            env = {runtime.ENV_ISOLATION: "container"}
            self.assertEqual(runtime.isolation_of(ws, env), "container")

    def test_manifest_wins_over_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _ws(tmp, isolation="container")
            env = {runtime.ENV_ISOLATION: "local"}
            self.assertEqual(runtime.isolation_of(ws, env), "container")


class TestContainerPath(unittest.TestCase):
    def test_root_and_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _ws(tmp)
            self.assertEqual(
                runtime._to_container_path(ws, str(ws.dir)), "/ws"
            )
            child = ws.dir / "inbox" / "f.txt"
            self.assertEqual(
                runtime._to_container_path(ws, str(child)),
                "/ws/inbox/f.txt",
            )

    def test_windows_separators_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _ws(tmp)
            win = str(ws.dir).replace("/", "\\") + "\\inbox\\x.txt"
            self.assertTrue(runtime._to_container_path(ws, win).startswith("/ws/"))

    def test_outside_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _ws(tmp)
            self.assertEqual(
                runtime._to_container_path(ws, "/elsewhere/x"), "/elsewhere/x"
            )

    def test_env_and_argv_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _ws(tmp)
            env = {"A": str(ws.dir), "B": "plain"}
            out = runtime.to_container_env(ws, env)
            self.assertEqual(out["A"], "/ws")
            self.assertEqual(out["B"], "plain")
            argv = runtime.to_container_argv(ws, [str(ws.dir / "f"), "--flag"])
            self.assertEqual(argv, ["/ws/f", "--flag"])


class TestPassthrough(unittest.TestCase):
    def test_allowlist_blocks_infra_and_unknown_agents_vars(self):
        keys = runtime._passthrough_keys(
            {
                "GH_TOKEN": "x",
                "OPENROUTER_API_KEY": "x",
                "AGENTS_WORKSPACE": "w",
                "AGENTS_BROWSER_PROFILE_NAME": "work",
                "AGENTS_SYNC_TOKEN": "secret-sync",
                "AGENTS_APPROVAL_TOKEN": "secret-app",
                "GIT_ASKPASS": "/tmp/askpass",
                "RELAY_SECRET": "rsec",
                "RELAY_WEB_TOKEN": "rweb",
                "TELEGRAM_BOT_TOKEN": "tbot",
                "USERPROFILE": "C:/Users/x",
                "RANDOM_SECRET": "x",
            }
        )
        self.assertIn("GH_TOKEN", keys)
        self.assertIn("OPENROUTER_API_KEY", keys)
        self.assertIn("AGENTS_WORKSPACE", keys)
        self.assertIn("AGENTS_BROWSER_PROFILE_NAME", keys)
        for blocked in (
            "AGENTS_SYNC_TOKEN",
            "AGENTS_APPROVAL_TOKEN",
            "GIT_ASKPASS",
            "RELAY_SECRET",
            "RELAY_WEB_TOKEN",
            "TELEGRAM_BOT_TOKEN",
            "USERPROFILE",
            "RANDOM_SECRET",
        ):
            self.assertNotIn(blocked, keys)

    def test_ensure_container_sets_hardening_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _ws(tmp, isolation="container")
            calls = []

            def fake_docker(*args):
                calls.append(args)
                if args[0] == "inspect":
                    return subprocess.CompletedProcess(args, 1, "", "")
                return subprocess.CompletedProcess(args, 0, "ok", "")

            with patch.object(runtime, "docker_bin", return_value="docker"), \
                    patch.object(runtime, "_docker", side_effect=fake_docker):
                runtime.ensure_container(ws, env={})
            run_args = [c for c in calls if c[0] == "run"][0]
            self.assertIn("--cap-drop=ALL", run_args)
            self.assertIn("--security-opt=no-new-privileges", run_args)


class TestSpawnLocal(unittest.TestCase):
    def test_local_spawn_is_subprocess(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _ws(tmp)
            proc = runtime.spawn(
                ws, ["python", "-c", "print(1)"], {}, pipes=True
            )
            out, _ = proc.communicate(timeout=10)
            self.assertEqual(out.strip(), "1")


class TestContainerGuards(unittest.TestCase):
    def test_container_without_docker_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = _ws(tmp, isolation="container")
            with patch.object(runtime, "docker_bin", return_value=None):
                with self.assertRaises(RuntimeError):
                    runtime.ensure_container(ws, env={})

    def test_sweep_no_docker_is_noop(self):
        with patch.object(runtime, "docker_bin", return_value=None):
            self.assertEqual(runtime.sweep(), [])


class TestDeleteStopsContainer(unittest.TestCase):
    def test_delete_stops_ws_container(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host(dir=Path(tmp))
            ws = host.create("qa")
            calls = []

            def fake_run(cmd, **kw):
                calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "", "")

            with patch("agents_workspace.workspace.subprocess.run", fake_run), \
                    patch.object(runtime, "docker_bin", return_value="docker"):
                host.delete("qa", hard=True)
            joined = [" ".join(c) for c in calls]
            self.assertTrue(
                any("rm" in c and f"klanker-ws-{ws.id}" in c for c in joined)
                or any(
                    "stop" in c and f"klanker-ws-{ws.id}" in c for c in joined
                ),
                joined,
            )
            self.assertFalse(ws.dir.exists())


if __name__ == "__main__":
    unittest.main()
