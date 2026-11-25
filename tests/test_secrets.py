"""Tests for host-only secret vault, precedence, expansion, and doctor."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from agents_workspace import (
    Host,
    delete_secret,
    doctor,
    expand_secrets,
    list_secret_names,
    resolve_secret,
    secret_inventory,
    secrets_dir,
    secrets_host_file,
    secrets_ws_file,
    write_secret,
)
from agents_workspace import secrets as secrets_mod


class TestHostSecretVault(unittest.TestCase):
    def _setup(self, tmp: str):
        host = Host.open(tmp)
        ws = host.create("w1")
        return host, ws

    def test_write_list_delete_host_and_ws(self):
        with tempfile.TemporaryDirectory() as tmp:
            host, ws = self._setup(tmp)
            p_host = write_secret("OPENAI_API_KEY", "sk-host-1", host=host, scope="host")
            p_ws = write_secret("OPENAI_API_KEY", "sk-ws-1", ws=ws, scope="workspace")
            self.assertEqual(p_host, secrets_host_file(host))
            self.assertEqual(p_ws, secrets_ws_file(ws))
            self.assertFalse((ws.dir / "secrets.json").exists())
            self.assertEqual(resolve_secret("OPENAI_API_KEY", ws, env={}), "sk-ws-1")
            self.assertEqual(
                resolve_secret("OPENAI_API_KEY", ws, env={}, scope="host"), "sk-host-1"
            )
            self.assertEqual(list_secret_names(host=host, ws=ws, scope="all"), ["OPENAI_API_KEY"])
            inv = secret_inventory(host=host, ws=ws)
            self.assertEqual(inv["host"], ["OPENAI_API_KEY"])
            self.assertEqual(inv["workspace"], ["OPENAI_API_KEY"])

            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(secrets_dir(host).stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(p_host.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(p_ws.stat().st_mode), 0o600)

            self.assertTrue(delete_secret("OPENAI_API_KEY", ws=ws, scope="workspace"))
            self.assertFalse(delete_secret("OPENAI_API_KEY", ws=ws, scope="workspace"))
            self.assertEqual(resolve_secret("OPENAI_API_KEY", ws, env={}), "sk-host-1")

    def test_infra_secret_ignores_ws_and_prefers_env_and_run_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            host, ws = self._setup(tmp)
            run_dir = Path(tmp) / "run_secrets"
            run_dir.mkdir()
            (run_dir / "RELAY_SECRET").write_text("from-run\n", encoding="utf-8")

            # Attempt to store infra secret in workspace scope fails cleanly.
            with self.assertRaises(ValueError):
                write_secret("RELAY_SECRET", "bad", ws=ws, scope="workspace")
            # Even planted legacy <ws>/secrets.json is ignored for INFRA_SECRET_KEYS.
            ws.secrets.write_text(json.dumps({"RELAY_SECRET": "poison"}), encoding="utf-8")
            write_secret("RELAY_SECRET", "from-host-file", host=host, scope="host")

            # Direct ENV wins even with file-first default.
            self.assertEqual(
                resolve_secret("RELAY_SECRET", ws, env={"RELAY_SECRET": "from-env"}),
                "from-env",
            )
            # /run/secrets wins over host.json.
            with patch.object(secrets_mod, "_RUN_SECRETS_DIR", run_dir):
                self.assertEqual(resolve_secret("RELAY_SECRET", ws, env={}), "from-run")
            # host.json fallback (never poisoned ws.secrets).
            with patch.object(secrets_mod, "_RUN_SECRETS_DIR", Path(tmp) / "nope"):
                self.assertEqual(
                    resolve_secret("RELAY_SECRET", ws, env={}), "from-host-file"
                )

    def test_env_first_order_flips_workspace_provider_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            host, ws = self._setup(tmp)
            write_secret("OPENAI_API_KEY", "ws-val", ws=ws, scope="workspace")
            write_secret("OPENAI_API_KEY", "host-val", host=host, scope="host")
            self.assertEqual(
                resolve_secret("OPENAI_API_KEY", ws, env={"OPENAI_API_KEY": "env-val"}),
                "ws-val",
            )
            self.assertEqual(
                resolve_secret(
                    "OPENAI_API_KEY",
                    ws,
                    env={
                        "OPENAI_API_KEY": "env-val",
                        "AGENTS_SECRETS_ORDER": "env-first",
                    },
                ),
                "env-val",
            )

    def test_optional_keyring_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            host, ws = self._setup(tmp)
            write_secret("OPENAI_API_KEY", "host-val", host=host, scope="host")
            fake_kr = types.SimpleNamespace(
                get_password=lambda svc, k: "kr-host"
                if svc == "agents-host" and k == "OPENAI_API_KEY"
                else ""
            )
            with patch.dict(sys.modules, {"keyring": fake_kr}):
                self.assertEqual(
                    resolve_secret(
                        "OPENAI_API_KEY", ws, env={"AGENTS_KEYRING": "1"}
                    ),
                    "kr-host",
                )
                self.assertEqual(
                    resolve_secret(
                        "OPENAI_API_KEY", ws, env={"AGENTS_KEYRING": "0"}
                    ),
                    "host-val",
                )

    def test_expand_secrets_trust_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            host, ws = self._setup(tmp)
            write_secret("MCP_API_KEY", "s3cr3t", host=host, scope="host")
            raw = {
                "command": "npx",
                "env": {"TOKEN": "${MCP_API_KEY}", "DEF": "${MISSING_VAR:-fallback}"},
            }
            untouched = expand_secrets(raw, ws=ws, env={}, trusted=False)
            self.assertEqual(untouched["env"]["TOKEN"], "${MCP_API_KEY}")
            expanded = expand_secrets(raw, ws=ws, env={}, trusted=True)
            self.assertEqual(expanded["env"]["TOKEN"], "s3cr3t")
            self.assertEqual(expanded["env"]["DEF"], "fallback")

    def test_doctor_reports_workspace_plaintext_leaks(self):
        with tempfile.TemporaryDirectory() as tmp:
            host, ws = self._setup(tmp)
            self.assertEqual(doctor(host)["status"], "ok")
            ws.secrets.write_text(json.dumps({"K": "v"}), encoding="utf-8")
            (ws.dir / ".env").write_text("SECRET=1\n", encoding="utf-8")
            ws.calendar.write_text(json.dumps({"password": "pw"}), encoding="utf-8")
            rep = doctor(host)
            self.assertEqual(rep["status"], "warn")
            paths = {i["path"] for i in rep["issues"]}
            self.assertIn(str(ws.secrets), paths)
            self.assertIn(str(ws.dir / ".env"), paths)
            self.assertIn(str(ws.calendar), paths)

    def test_explicit_host_read_write_symmetry(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            ws = host.create("work")
            write_secret(
                "OPENAI_API_KEY",
                "sk-explicit-host-ws",
                host=host,
                scope="workspace",
                env={"AGENTS_WORKSPACE": "work"},
            )
            # auto scope still resolves the ws vault file ...
            self.assertEqual(
                resolve_secret(
                    "OPENAI_API_KEY",
                    host=host,
                    env={"AGENTS_WORKSPACE": "work"},
                ),
                "sk-explicit-host-ws",
            )
            # ... and scope="workspace" roundtrips through the same
            # host-vault ws-<id>.json write_secret just stored to (R02).
            self.assertEqual(
                resolve_secret(
                    "OPENAI_API_KEY",
                    host=host,
                    scope="workspace",
                    env={"AGENTS_WORKSPACE": "work"},
                ),
                "sk-explicit-host-ws",
            )
            # the vault wins over the legacy <ws>/secrets.json fallback.
            ws.secrets.write_text(
                json.dumps({"OPENAI_API_KEY": "sk-ws-local"}),
                encoding="utf-8",
            )
            self.assertEqual(
                resolve_secret(
                    "OPENAI_API_KEY",
                    host=host,
                    scope="workspace",
                    env={"AGENTS_WORKSPACE": "work"},
                ),
                "sk-explicit-host-ws",
            )
            # with the vault key deleted, the legacy file is still read.
            self.assertTrue(
                delete_secret(
                    "OPENAI_API_KEY",
                    host=host,
                    scope="workspace",
                    env={"AGENTS_WORKSPACE": "work"},
                )
            )
            self.assertEqual(
                resolve_secret(
                    "OPENAI_API_KEY",
                    host=host,
                    scope="workspace",
                    env={"AGENTS_WORKSPACE": "work"},
                ),
                "sk-ws-local",
            )

    def test_concurrent_write_secret_loses_no_keys(self):
        """Locked RMW (R02): N threads writing host.json drop zero keys."""
        import threading

        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            threads, keys_per_thread = 8, 6
            barrier = threading.Barrier(threads)

            def worker(t: int) -> None:
                barrier.wait()
                for k in range(keys_per_thread):
                    write_secret(
                        f"KEY_T{t}_K{k}", f"v-{t}-{k}", host=host, scope="host"
                    )

            pool = [
                threading.Thread(target=worker, args=(t,))
                for t in range(threads)
            ]
            for th in pool:
                th.start()
            for th in pool:
                th.join()

            doc = json.loads(
                secrets_host_file(host).read_text(encoding="utf-8")
            )
            self.assertEqual(len(doc), threads * keys_per_thread)
            for t in range(threads):
                for k in range(keys_per_thread):
                    self.assertEqual(doc[f"KEY_T{t}_K{k}"], f"v-{t}-{k}")


class TestWorkspaceScopeBoundary(unittest.TestCase):
    """R02 (fixes agents-workspace#44): scope="workspace" searches ONLY
    this workspace's own <host>/secrets/ws-<id>.json, then the legacy
    <ws>/secrets.json — never host.json, another ws-<other>.json,
    keyring, /run/secrets, or env."""

    def _setup(self, tmp: str):
        host = Host.open(tmp)
        ws = host.create("w1")
        return host, ws

    def test_workspace_scope_reads_own_vault_then_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            host, ws = self._setup(tmp)
            write_secret("OPENAI_API_KEY", "host-v", host=host, scope="host")
            write_secret("OPENAI_API_KEY", "ws-vault", ws=ws, scope="workspace")
            env = {"OPENAI_API_KEY": "env-v"}
            # host vault + env exist, yet workspace scope sees only its
            # own ws-<id>.json — the value write_secret just stored.
            self.assertEqual(
                resolve_secret("OPENAI_API_KEY", ws, env=env, scope="workspace"),
                "ws-vault",
            )
            # legacy <ws>/secrets.json is fallback only — vault wins.
            ws.secrets.write_text(
                json.dumps({"OPENAI_API_KEY": "ws-local"}), encoding="utf-8"
            )
            self.assertEqual(
                resolve_secret("OPENAI_API_KEY", ws, env=env, scope="workspace"),
                "ws-vault",
            )
            self.assertTrue(
                delete_secret("OPENAI_API_KEY", ws=ws, scope="workspace")
            )
            self.assertEqual(
                resolve_secret("OPENAI_API_KEY", ws, env=env, scope="workspace"),
                "ws-local",
            )

    def test_workspace_scope_isolates_other_workspaces_and_host(self):
        """Oracle: ws2 can never see ws1's vault; host.json stays out."""
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            ws1 = host.create("w1")
            ws2 = host.create("w2")
            write_secret("TOK", "val", ws=ws1, scope="workspace")
            write_secret("HOST_ONLY", "hv", host=host, scope="host")
            self.assertEqual(
                resolve_secret("TOK", ws=ws1, scope="workspace"), "val"
            )
            self.assertEqual(
                resolve_secret("HOST_ONLY", ws=ws1, scope="workspace"), ""
            )
            self.assertEqual(
                resolve_secret("TOK", ws=ws2, scope="workspace"), ""
            )
            self.assertEqual(
                resolve_secret(
                    "TOK", ws=ws2, env={"TOK": "env-tok"}, scope="workspace"
                ),
                "",
            )

    def test_workspace_scope_blocks_infra_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            host, ws = self._setup(tmp)
            write_secret("RELAY_SECRET", "host-rs", host=host, scope="host")
            env = {"RELAY_SECRET": "env-rs"}
            self.assertEqual(
                resolve_secret("RELAY_SECRET", ws, env=env, scope="workspace"),
                "",
            )
            # oracle: "" even with RELAY_SECRET in host vault AND os.environ
            with patch.dict(os.environ, {"RELAY_SECRET": "os-env-rs"}):
                self.assertEqual(
                    resolve_secret("RELAY_SECRET", ws=ws, scope="workspace"),
                    "",
                )
            # even a planted ws-local INFRA key never resolves
            ws.secrets.write_text(
                json.dumps({"RELAY_SECRET": "poison"}), encoding="utf-8"
            )
            self.assertEqual(
                resolve_secret("RELAY_SECRET", ws, env=env, scope="workspace"),
                "",
            )

    def test_expand_secrets_workspace_scope_blocks_infra(self):
        with tempfile.TemporaryDirectory() as tmp:
            host, ws = self._setup(tmp)
            write_secret("RELAY_SECRET", "rs", host=host, scope="host")
            write_secret("OPENAI_API_KEY", "host-ok", host=host, scope="host")
            ws.secrets.write_text(
                json.dumps({"MY_TOKEN": "ws-tok"}), encoding="utf-8"
            )
            env = {"RELAY_SECRET": "env-rs", "OPENAI_API_KEY": "env-ok"}
            out = expand_secrets(
                {
                    "infra": "${RELAY_SECRET}",
                    "wslocal": "${MY_TOKEN}",
                    "fallback": "${OPENAI_API_KEY:-dflt}",
                },
                ws=ws,
                env=env,
                trusted=True,
                scope="workspace",
            )
            self.assertEqual(out["infra"], "")
            self.assertEqual(out["wslocal"], "ws-tok")
            self.assertEqual(out["fallback"], "dflt")
            # auto scope still resolves host-level values
            out2 = expand_secrets(
                {"infra": "${RELAY_SECRET}"}, ws=ws, env=env, trusted=True
            )
            self.assertEqual(out2["infra"], "env-rs")
            with self.assertRaises(ValueError):
                expand_secrets("${X}", ws=ws, trusted=True, scope="bogus")

    def test_doctor_flags_secretish_keys_inside_list_items(self):
        """R02: repos:[{api_key}] nested under a list is still caught."""
        import warnings

        with tempfile.TemporaryDirectory() as tmp:
            host, ws = self._setup(tmp)
            meta = json.loads(ws.workspace_file.read_text(encoding="utf-8"))
            meta["repos"] = [{"slug": "a", "api_key": "x"}]
            ws.workspace_file.write_text(json.dumps(meta), encoding="utf-8")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                rep = doctor(host)
            self.assertEqual(rep["status"], "warn")
            msgs = " ".join(i["message"] for i in rep["issues"])
            self.assertIn("repos.api_key", msgs)

    def test_doctor_flags_local_policy_with_sync_hosts_once(self):
        """R02: hand-edited local+allowlist -> policy_error + one issue."""
        import warnings

        with tempfile.TemporaryDirectory() as tmp:
            host, ws = self._setup(tmp)
            meta = json.loads(ws.workspace_file.read_text(encoding="utf-8"))
            meta["sync_policy"] = "local"
            meta["sync_hosts"] = ["dev-1"]
            ws.workspace_file.write_text(json.dumps(meta), encoding="utf-8")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                bad = host.workspace("w1")
                rep = doctor(host)
            # runtime still fails closed, but the contradiction surfaces.
            self.assertFalse(bad.is_syncable())
            self.assertFalse(bad.allows_device("dev-1"))
            self.assertTrue(bad.policy_error)
            self.assertEqual(rep["status"], "warn")
            msgs = [
                i["message"]
                for i in rep["issues"]
                if i["path"] == str(ws.workspace_file)
            ]
            self.assertEqual(len(msgs), 1, msgs)
            self.assertIn("sync_hosts", msgs[0])

    def test_doctor_flags_malformed_policy_and_secretish_keys(self):
        import warnings

        with tempfile.TemporaryDirectory() as tmp:
            host, ws = self._setup(tmp)
            meta = json.loads(ws.workspace_file.read_text(encoding="utf-8"))
            meta["sync_policy"] = "everything"
            meta["sync_hosts"] = "my-laptop"
            meta["MY_API_TOKEN"] = "leak"
            ws.workspace_file.write_text(json.dumps(meta), encoding="utf-8")
            ws.config.write_text(
                json.dumps({"nested": {"MY_SECRET": "x"}}), encoding="utf-8"
            )
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                rep = doctor(host)
            self.assertEqual(rep["status"], "warn")
            wsj_msgs = [
                i["message"] for i in rep["issues"]
                if i["path"] == str(ws.workspace_file)
            ]
            self.assertTrue(any("sync_policy" in m for m in wsj_msgs), wsj_msgs)
            self.assertTrue(any("sync_hosts" in m for m in wsj_msgs), wsj_msgs)
            self.assertTrue(any("MY_API_TOKEN" in m for m in wsj_msgs), wsj_msgs)
            cfg_issues = [
                i for i in rep["issues"] if i["path"] == str(ws.config)
            ]
            self.assertTrue(cfg_issues)
            self.assertIn("MY_SECRET", cfg_issues[0]["message"])


class TestResolveRelayToken(unittest.TestCase):
    def test_relay_secret_then_web_token_order(self):
        from agents_workspace import resolve_relay_token

        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            write_secret("RELAY_SECRET", "rs", host=host, scope="host")
            write_secret("RELAY_WEB_TOKEN", "wt", host=host, scope="host")
            self.assertEqual(resolve_relay_token(host=host, env={}), "rs")

    def test_web_token_only_returns_it(self):
        from agents_workspace import resolve_relay_token

        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            write_secret("RELAY_WEB_TOKEN", "wt-only", host=host, scope="host")
            self.assertEqual(resolve_relay_token(host=host, env={}), "wt-only")

    def test_env_relay_secret(self):
        from agents_workspace import resolve_relay_token

        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            self.assertEqual(
                resolve_relay_token(host=host, env={"RELAY_SECRET": "env-rs"}),
                "env-rs",
            )

    def test_gateway_secret_legacy_warns(self):
        import warnings

        from agents_workspace import resolve_relay_token

        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            write_secret("GATEWAY_SECRET", "legacy-gw", host=host, scope="host")
            with warnings.catch_warnings(record=True) as seen:
                warnings.simplefilter("always")
                self.assertEqual(resolve_relay_token(host=host, env={}), "legacy-gw")
            self.assertTrue(
                any(w.category is DeprecationWarning for w in seen)
            )

    def test_no_token_returns_empty(self):
        from agents_workspace import resolve_relay_token

        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            self.assertEqual(resolve_relay_token(host=host, env={}), "")


if __name__ == "__main__":
    unittest.main()
