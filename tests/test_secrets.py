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
            self.assertEqual(
                resolve_secret(
                    "OPENAI_API_KEY",
                    host=host,
                    scope="workspace",
                    env={"AGENTS_WORKSPACE": "work"},
                ),
                "sk-explicit-host-ws",
            )


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
