"""Host/Workspace resolver tests."""

from __future__ import annotations

import json
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

    def test_host_home_alias_removed(self):
        # AGENTS_HOME is dead: no longer read, ignored silently.
        self.assertEqual(host_dir({"AGENTS_HOME": "/tmp/h"}), Path.home() / ".agents")
        self.assertEqual(
            host_dir({"AGENTS_HOST_DIR": "/tmp/new", "AGENTS_HOME": "/tmp/old"}),
            _p("/tmp/new"),
        )

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

    def test_process_override_never_reaches_other_workspaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("a")
            host.create("b")
            import os as _os
            from unittest.mock import patch as _patch

            with _patch.dict(_os.environ, {"AGENTS_MEMORY_PATH": "/tmp/extmem"}):
                self.assertEqual(
                    host.workspace("b").memory,
                    Path(tmp) / "workspaces" / "b" / "memory",
                )
                self.assertEqual(
                    [str(ws.memory) for ws in host.list_workspaces()],
                    [
                        str(Path(tmp) / "workspaces" / "a" / "memory"),
                        str(Path(tmp) / "workspaces" / "b" / "memory"),
                    ],
                )
                # open() (deprecated ad-hoc) also ignores process overrides.
                ws = Workspace.open(tmp + "/adhoc")
                self.assertEqual(ws.memory, Path(tmp).resolve() / "adhoc" / "memory")

    def test_override_warns_on_cli_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            Host.open(tmp).create("a")
            env = {"AGENTS_HOST_DIR": tmp, "AGENTS_WORKSPACE": "a",
                   "AGENTS_MEMORY_PATH": "/tmp/extmem"}
            with warnings.catch_warnings(record=True) as seen:
                warnings.simplefilter("always")
                Workspace.from_env(env)
            self.assertTrue(
                any(w.category is UserWarning and "AGENTS_MEMORY_PATH" in str(w.message)
                    for w in seen),
                [str(w.message) for w in seen],
            )

    def test_canonical_override_does_not_warn(self):
        """An override equal to the resolved path is a pin, not an override."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Host.open(tmp).create("a")
            env = {
                "AGENTS_HOST_DIR": tmp,
                "AGENTS_WORKSPACE": "a",
                "AGENTS_MEMORY_DIR": str(ws.dir / "memory"),
                "AGENTS_TRACES_DIR": str(ws.dir / "traces"),
                "AGENTS_USERS_FILE": str(Path(tmp) / "users.json"),
            }
            with warnings.catch_warnings(record=True) as seen:
                warnings.simplefilter("always")
                Workspace.from_env(env)
            self.assertFalse(
                any("domain env overrides" in str(w.message) for w in seen),
                [str(w.message) for w in seen],
            )

    def test_env_strips_foreign_and_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            a = host.create("a")
            b = host.create("b")
            poison = {
                "AGENTS_HOST_DIR": str(Path(tmp) / "other-host"),
                "AGENTS_HOME": str(Path(tmp) / "other-home"),
                "AGENTS_WORKSPACE": "b",
                "AGENTS_WORKSPACE_ROOT": str(b.dir),
                "AGENTS_WORKSPACE_DIR": str(b.dir / "other-work"),
                "AGENTS_WORKSPACES_DIR": str(Path(tmp) / "other-ws"),
                "AGENTS_DIR": str(b.dir),
                "AGENTS_WORKDIR": str(b.dir / "wd"),
                "AGENTS_PERSONA": "someone",
                "AGENTS_MEMORY_PATH": str(b.memory),
                "AGENTS_MEMORY_DIR": str(b.memory),
                "AGENTS_RELAY_STATE": str(b.relay_state),
                "AGENTS_RELAY_DIR": str(b.relay_state),
                "AGENTS_IDENTITY_PATH": str(b.dir / "id.json"),
                "AGENTS_KEYS_DIR": str(b.dir / "keys"),
                "KEEP_ME": "yes",
            }
            child = a.env(poison)
            self.assertEqual(child["KEEP_ME"], "yes")
            self.assertEqual(child["AGENTS_HOST_DIR"], str(Path(tmp).resolve()))
            self.assertEqual(child["AGENTS_WORKSPACE"], "a")
            self.assertEqual(child["AGENTS_WORKDIR"], str(a.workdir))
            self.assertNotIn("AGENTS_PERSONA", child)
            for key in (
                "AGENTS_HOME", "AGENTS_WORKSPACE_ROOT", "AGENTS_WORKSPACE_DIR",
                "AGENTS_WORKSPACES_DIR", "AGENTS_DIR",
            ):
                self.assertNotIn(key, child, key)
            # every resolver-owned value points inside workspace a (or the
            # shared host), never inside workspace b
            b_str = str(b.dir)
            for key, value in child.items():
                if key.startswith("AGENTS_"):
                    self.assertFalse(
                        str(value).startswith(b_str),
                        f"{key} leaks into b: {value}",
                    )
            # new + alias names agree
            self.assertEqual(child["AGENTS_MEMORY_DIR"], str(a.memory))
            self.assertEqual(child["AGENTS_MEMORY_PATH"], str(a.memory))
            self.assertEqual(child["AGENTS_RELAY_DIR"], str(a.relay_state))
            self.assertEqual(child["AGENTS_RELAY_STATE"], str(a.relay_state))
            self.assertEqual(child["AGENTS_KEYS_DIR"], str(host.keys))
            self.assertEqual(child["AGENTS_USERS_FILE"], str(host.users_file()))
            self.assertEqual(child["AGENTS_IDENTITY_PATH"], str(host.users_file()))
            self.assertEqual(child["AGENTS_BROWSER_PROFILE_NAME"], a.browser_profile)
            # persona + workdir params
            child2 = a.env(poison, persona="ops", workdir="/tmp/wd2")
            self.assertEqual(child2["AGENTS_PERSONA"], "ops")
            self.assertEqual(child2["AGENTS_WORKDIR"], str(Path("/tmp/wd2").resolve()))

    def test_users_file_override_is_test_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("a")
            ws = host.workspace("a")
            self.assertEqual(ws.identity, Path(tmp) / "users.json")
            env = {"AGENTS_USERS_FILE": "/tmp/u.json"}
            self.assertEqual(host.users_file(env), _p("/tmp/u.json"))


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
            self.assertTrue(ws.workspace_file.is_file())
            self.assertTrue((ws.personas / "default" / "persona.json").is_file())
            # Strict lazy: domain dirs are not materialized until written or ensured.
            for lazy_dir in (ws.memory, ws.traces, ws.work, ws.skills):
                self.assertFalse(lazy_dir.exists(), lazy_dir.name)
            ws.ensure()
            for name in ("memory", "traces", "personas", "work"):
                self.assertTrue(ws.domain(name).is_dir(), name)

    def test_canonical_host_and_ws_paths(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            self.assertEqual(host.approval_allowlist, host.dir / "approval-allowlist.json")
            self.assertEqual(host.providers, host.dir / "providers")
            self.assertEqual(host.mcp_dir, host.dir / "mcp")
            ws = host.create("a")
            self.assertEqual(ws.run, ws.dir / "run")
            self.assertEqual(ws.delegates, ws.dir / "run" / "delegates")
            self.assertEqual(ws.procs, ws.dir / "run" / "procs")
            self.assertEqual(ws.domain("run"), ws.run)
            self.assertEqual(ws.browser_profile, "default")
            meta = json.loads(ws.workspace_file.read_text(encoding="utf-8"))
            meta["browser_profile"] = "work-profile"
            ws.workspace_file.write_text(json.dumps(meta), encoding="utf-8")
            self.assertEqual(ws.browser_profile, "work-profile")

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

    def test_delete_archives_to_trash(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("a")
            out = host.delete("a")
            self.assertFalse((host.workspaces_dir / "a").exists())
            self.assertTrue((out / "workspace.json").is_file())
            self.assertEqual(out.parent.name, ".trash")
            self.assertEqual(host.list_workspaces(), [])

    def test_delete_hard_removes(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("a")
            host.delete("a", hard=True)
            self.assertFalse((host.workspaces_dir / "a").exists())
            self.assertFalse((host.workspaces_dir / ".trash").exists())

    def test_delete_refuses_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("a")
            env = {"AGENTS_WORKSPACE": "a"}
            with self.assertRaises(ValueError):
                host.delete("a", env=env)

    def test_delete_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            with self.assertRaises(ValueError):
                host.delete("nope")


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


class TestDomainAliases(unittest.TestCase):
    def test_memory_dir_new_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host(dir=Path(tmp) / "host")
            host.create("w1")
            env = {"AGENTS_HOST_DIR": str(host.dir), "AGENTS_WORKSPACE": "w1",
                   "AGENTS_MEMORY_DIR": tmp + "/new", "AGENTS_MEMORY_PATH": tmp + "/old"}
            ws = Workspace.from_env(env)
            self.assertEqual(ws.memory, Path(tmp + "/new").resolve())

    def test_memory_path_alias_warns(self):
        import warnings
        with tempfile.TemporaryDirectory() as tmp:
            host = Host(dir=Path(tmp) / "host")
            host.create("w1")
            env = {"AGENTS_HOST_DIR": str(host.dir), "AGENTS_WORKSPACE": "w1",
                   "AGENTS_MEMORY_PATH": tmp + "/old"}
            with warnings.catch_warnings(record=True) as seen:
                warnings.simplefilter("always")
                ws = Workspace.from_env(env)
                self.assertEqual(ws.memory, Path(tmp + "/old").resolve())
            self.assertTrue(any("AGENTS_MEMORY_PATH is deprecated" in str(w.message) for w in seen))

    def test_read_alias_helper(self):
        import warnings
        from agents_workspace.compat import read_alias
        self.assertEqual(read_alias({"NEW": "a", "OLD": "b"}, "NEW", "OLD"), "a")
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            self.assertEqual(read_alias({"OLD": "b"}, "NEW", "OLD"), "b")
        self.assertTrue(any("deprecated" in str(w.message) for w in seen))
        self.assertEqual(read_alias({}, "NEW", "OLD"), "")


class TestAllowedRoots(unittest.TestCase):
    def test_ws_tree_and_host_infra(self):
        from agents_workspace import allowed_roots, denied_paths

        with tempfile.TemporaryDirectory() as tmp:
            ws = Host.open(tmp).workspace("a")
            roots = allowed_roots(ws, env={})
            denied = denied_paths(ws)
            self.assertIn(ws.dir, roots)
            for path in (ws.host.run, ws.host.cache):
                self.assertIn(path, roots)
            for excluded in (ws.host.logs, ws.host.clients):
                self.assertNotIn(excluded, roots)
                self.assertIn(excluded, denied)
            for secret_path in (
                ws.host.keys, ws.host.secrets, ws.host.dir / "secrets",
                ws.host.mcp_json, ws.host.dir / "mcp",
                ws.host.agents_json, ws.host.users_file(),
                ws.host.dir / "approval-allowlist.json",
                ws.host.run / "approvals",
                ws.host.logs,
                ws.host.clients,
                ws.secrets, ws.members,
            ):
                self.assertNotIn(secret_path, roots)
                self.assertIn(secret_path, denied)
            # the caller adds the workdir root itself (terminal only)
            self.assertNotIn(ws.workdir, roots)

    def test_env_listed_dirs(self):
        from agents_workspace import allowed_roots

        ws = Host.open("/tmp/h").workspace("a")
        env = {
            "AGENTS_SKILLS_DIR": "/tmp/sk",
            "AGENTS_MEMORY_PATH": "/tmp/mem",
            "AGENTS_SKILLS_EXTRA": f"/tmp/e1{os.pathsep}/tmp/e2",
            "AGENTS_WORKDIR_ALLOW": "/tmp/ok",
        }
        roots = allowed_roots(ws, env=env)
        for raw in ("/tmp/sk", "/tmp/mem", "/tmp/e1", "/tmp/e2", "/tmp/ok"):
            self.assertIn(Path(raw).expanduser(), roots)

    def test_allow_legacy_alias_warns(self):
        from agents_workspace import allowed_roots

        ws = Host.open("/tmp/h").workspace("a")
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            roots = allowed_roots(ws, env={"AGENTS_WORKSPACE_ALLOW": "/tmp/old"})
        self.assertIn(Path("/tmp/old").expanduser(), roots)
        self.assertTrue(
            any("AGENTS_WORKSPACE_ALLOW is deprecated" in str(w.message) for w in seen)
        )


class TestResolveSecret(unittest.TestCase):
    def _ws(self, tmp: str) -> Workspace:
        host = Host.open(tmp)
        ws = host.workspace("a")
        ws.dir.mkdir(parents=True, exist_ok=True)
        host.dir.mkdir(parents=True, exist_ok=True)
        return ws

    def test_ws_beats_host_beats_env(self):
        import json
        from agents_workspace import resolve_secret

        with tempfile.TemporaryDirectory() as tmp:
            ws = self._ws(tmp)
            ws.host.secrets.write_text(json.dumps({"K": "host"}), encoding="utf-8")
            self.assertEqual(
                resolve_secret("K", ws, env={"K": "env"}), "host"
            )
            ws.secrets.write_text(json.dumps({"K": "ws"}), encoding="utf-8")
            self.assertEqual(resolve_secret("K", ws, env={"K": "env"}), "ws")

    def test_env_fallback_and_missing(self):
        from agents_workspace import resolve_secret

        with tempfile.TemporaryDirectory() as tmp:
            ws = self._ws(tmp)
            self.assertEqual(resolve_secret("K", ws, env={"K": "env"}), "env")
            self.assertEqual(resolve_secret("NOPE", ws, env={}), "")

    def test_bad_json_ignored(self):
        from agents_workspace import resolve_secret

        with tempfile.TemporaryDirectory() as tmp:
            ws = self._ws(tmp)
            ws.secrets.write_text("{broken", encoding="utf-8")
            ws.host.secrets.write_text('["not","a","dict"]', encoding="utf-8")
            self.assertEqual(resolve_secret("K", ws, env={"K": "env"}), "env")


class TestDelegateEnv(unittest.TestCase):
    def test_depth_parse_and_increment(self):
        from agents_workspace import delegate_depth, delegate_env

        self.assertEqual(delegate_depth({}), 0)
        self.assertEqual(
            delegate_depth({"AGENTS_DELEGATE_DEPTH": "3"}), 3)
        self.assertEqual(
            delegate_depth({"AGENTS_DELEGATE_DEPTH": "junk"}), 0)
        child = delegate_env({"AGENTS_DELEGATE_DEPTH": "3",
                              "OTHER": "keep"})
        self.assertEqual(child["AGENTS_DELEGATE_DEPTH"], "4")
        self.assertEqual(child["OTHER"], "keep")

    def test_limit_refuses_in_parent(self):
        from agents_workspace import delegate_env

        with self.assertRaises(ValueError):
            delegate_env({"AGENTS_DELEGATE_DEPTH": "8"})
        with self.assertRaises(ValueError):
            delegate_env({"AGENTS_DELEGATE_DEPTH": "2"}, max_depth=2)
        # env-set limit honored
        with self.assertRaises(ValueError):
            delegate_env({"AGENTS_DELEGATE_DEPTH": "3",
                          "AGENTS_DELEGATE_MAX_DEPTH": "3"})
        # one below the limit still passes
        out = delegate_env({"AGENTS_DELEGATE_DEPTH": "2",
                            "AGENTS_DELEGATE_MAX_DEPTH": "3"})
        self.assertEqual(out["AGENTS_DELEGATE_DEPTH"], "3")

    def test_workspace_env_preserves_depth(self):
        from agents_workspace import delegate_env

        with tempfile.TemporaryDirectory() as tmp:
            ws = Host.open(tmp).workspace("a")
            child = ws.env(
                {"AGENTS_DELEGATE_DEPTH": "2"})
            self.assertEqual(child["AGENTS_DELEGATE_DEPTH"], "2")
            nxt = delegate_env(child)
            self.assertEqual(nxt["AGENTS_DELEGATE_DEPTH"], "3")


class TestSelectWorkspaceDefaults(unittest.TestCase):
    def test_multi_ws_selects_default_or_configured_unless_strict(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("default")
            host.create("work")
            self.assertEqual(host.select_workspace().id, "default")
            with self.assertRaises(ValueError):
                host.select_workspace(strict=True)
            host.agents_json.write_text(
                json.dumps({"default_workspace": "work"}), encoding="utf-8"
            )
            self.assertEqual(host.select_workspace().id, "work")
            host.agents_json.write_text(
                json.dumps({"default_workspace": "missing"}), encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                host.select_workspace()

    def test_host_and_workspace_config_helpers(self):
        import json
        from agents_workspace import host_config, workspace_config

        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.dir.mkdir(parents=True, exist_ok=True)
            host.agents_json.write_text(json.dumps({"node_id": "n1"}), encoding="utf-8")
            ws = host.create("alpha")
            self.assertEqual(host_config(host)["node_id"], "n1")
            self.assertEqual(workspace_config(ws)["id"], "alpha")


class TestRoleScopedEnv(unittest.TestCase):
    def test_role_profiles_strip_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Host.open(tmp).create("a")
            base = {
                "PATH": "/bin",
                "RELAY_SECRET": "rsec",
                "RELAY_WEB_TOKEN": "wtok",
                "TELEGRAM_BOT_TOKEN": "ttok",
                "AGENTS_SYNC_TOKEN": "st",
                "AGENTS_APPROVAL_TOKEN": "aptok",
                "GITHUB_TOKEN": "ght",
                "GIT_ASKPASS": "/tmp/askpass.sh",
                "OPENAI_API_KEY": "ok",
                "ANTHROPIC_API_KEY": "ak",
                "LLM_API_KEY": "lk",
            }
            turn = ws.env(base, role="turn", provider_key_env="OPENAI_API_KEY")
            self.assertNotIn("RELAY_SECRET", turn)
            self.assertNotIn("RELAY_WEB_TOKEN", turn)
            self.assertNotIn("TELEGRAM_BOT_TOKEN", turn)
            self.assertNotIn("AGENTS_SYNC_TOKEN", turn)
            self.assertEqual(turn["OPENAI_API_KEY"], "ok")
            self.assertNotIn("ANTHROPIC_API_KEY", turn)
            self.assertNotIn("LLM_API_KEY", turn)

            tool = ws.env(base, role="tool", extra_allow=("GITHUB_TOKEN",))
            for k in ("RELAY_SECRET", "TELEGRAM_BOT_TOKEN", "OPENAI_API_KEY",
                      "ANTHROPIC_API_KEY", "LLM_API_KEY", "AGENTS_APPROVAL_TOKEN",
                      "AGENTS_SYNC_TOKEN", "GIT_ASKPASS"):
                self.assertNotIn(k, tool)
            self.assertEqual(tool["GITHUB_TOKEN"], "ght")

            acp = ws.env(base, role="acp")
            for k in ("RELAY_SECRET", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                      "LLM_API_KEY", "GITHUB_TOKEN", "AGENTS_SYNC_TOKEN"):
                self.assertNotIn(k, acp)

            container = ws.env(base, role="container", provider_key_env="ANTHROPIC_API_KEY")
            self.assertNotIn("RELAY_SECRET", container)
            self.assertEqual(container["ANTHROPIC_API_KEY"], "ak")
            self.assertNotIn("OPENAI_API_KEY", container)

            with self.assertRaises(ValueError):
                ws.env(base, role="bogus")


class TestHostIdAndServeState(unittest.TestCase):
    def test_host_id_persists_and_mints(self):
        import json
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.dir.mkdir(parents=True, exist_ok=True)
            with patch(
                "agents_workspace.workspace._agents_keys_did", return_value=""
            ):
                hid = host.host_id()
            self.assertTrue(hid.startswith("host_"))
            self.assertEqual(host.host_id(), hid)
            data = json.loads(host.agents_json.read_text(encoding="utf-8"))
            self.assertEqual(data["host_id"], hid)

    def test_host_id_preserves_agents_json_keys(self):
        import json
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.dir.mkdir(parents=True, exist_ok=True)
            host.agents_json.write_text(
                json.dumps({"default_workspace": "alpha"}), encoding="utf-8"
            )
            with patch(
                "agents_workspace.workspace._agents_keys_did", return_value=""
            ):
                hid = host.host_id()
            data = json.loads(host.agents_json.read_text(encoding="utf-8"))
            self.assertEqual(data["default_workspace"], "alpha")
            self.assertEqual(data["host_id"], hid)

    def test_host_id_survives_corrupt_agents_json(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.dir.mkdir(parents=True, exist_ok=True)
            host.agents_json.write_text("{corrupt", encoding="utf-8")
            with patch(
                "agents_workspace.workspace._agents_keys_did", return_value=""
            ):
                hid = host.host_id()
            self.assertTrue(hid.startswith("host_"))
            self.assertEqual(host.host_id(), hid)

    def test_host_id_prefers_did_lookup(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.dir.mkdir(parents=True, exist_ok=True)
            with patch(
                "agents_workspace.workspace._agents_keys_did",
                return_value="did:key:z6Mkabc",
            ):
                self.assertEqual(host.host_id(), "did:key:z6Mkabc")

    def test_serve_state_roundtrip_and_clear(self):
        import json

        from agents_workspace import (
            clear_serve_state,
            read_serve_state,
            write_serve_state,
        )

        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            self.assertIsNone(read_serve_state(host))
            path = write_serve_state(host, pid=1234, port=8787, url="http://127.0.0.1:8787")
            self.assertEqual(path, host.run / "serve.json")
            state = read_serve_state(host)
            self.assertEqual(state["pid"], 1234)
            self.assertEqual(state["port"], 8787)
            self.assertEqual(state["url"], "http://127.0.0.1:8787")
            self.assertEqual(state["host_id"], host.host_id())
            # Falsifying: wrong expected pid must not delete the state file.
            self.assertFalse(clear_serve_state(host, expected_pid=9999))
            self.assertTrue(path.is_file())
            self.assertTrue(clear_serve_state(host, expected_pid=1234))
            self.assertFalse(path.is_file())
            self.assertFalse(clear_serve_state(host))

    def test_read_serve_state_malformed_and_legacy_pid(self):
        import warnings

        from agents_workspace import read_serve_state, write_serve_state

        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.run.mkdir(parents=True, exist_ok=True)
            host.serve_state_path.write_text("{bad", encoding="utf-8")
            self.assertIsNone(read_serve_state(host))

        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.run.mkdir(parents=True, exist_ok=True)
            (host.run / "serve.pid").write_text("4321\n", encoding="utf-8")
            with warnings.catch_warnings(record=True) as seen:
                warnings.simplefilter("always")
                state = read_serve_state(host)
            self.assertEqual(state, {"pid": 4321})
            self.assertTrue(
                any(w.category is DeprecationWarning for w in seen)
            )

    def test_write_serve_state_explicit_host_id(self):
        from agents_workspace import read_serve_state, write_serve_state

        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            write_serve_state(
                host, pid=1, port=9, url="http://x", host_id="host_fixed"
            )
            self.assertEqual(read_serve_state(host)["host_id"], "host_fixed")


class TestResolveEntryWorkspace(unittest.TestCase):
    def test_explicit_env_wins_and_unknown_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("alpha")
            host.create("beta")
            ws = host.resolve_entry_workspace({"AGENTS_WORKSPACE": "beta"})
            self.assertEqual(ws.id, "beta")
            with self.assertRaises(ValueError):
                host.resolve_entry_workspace({"AGENTS_WORKSPACE": "missing"})

    def test_default_workspace_config(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("alpha")
            host.create("beta")
            host.dir.mkdir(parents=True, exist_ok=True)
            host.agents_json.write_text(
                json.dumps({"default_workspace": "beta"}), encoding="utf-8"
            )
            self.assertEqual(host.resolve_entry_workspace({}).id, "beta")

    def test_missing_default_falls_through(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("alpha")
            host.dir.mkdir(parents=True, exist_ok=True)
            host.agents_json.write_text(
                json.dumps({"default_workspace": "ghost"}), encoding="utf-8"
            )
            # configured-but-missing default must not raise; falls to "alpha"
            self.assertEqual(host.resolve_entry_workspace({}).id, "alpha")

    def test_multi_workspace_no_default_picks_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("beta")
            host.create("alpha")
            # Falsifying: multi-workspace without default must not crash.
            self.assertEqual(host.resolve_entry_workspace({}).id, "alpha")

    def test_existing_default_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("zzz")
            host.create("default")
            self.assertEqual(host.resolve_entry_workspace({}).id, "default")

    def test_empty_host_creates_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            ws = host.resolve_entry_workspace({})
            self.assertEqual(ws.id, "default")
            self.assertTrue(host.workspace_exists("default"))


class TestWorkspaceWorkdirSchema(unittest.TestCase):
    def test_workdir_and_lock_emitted_per_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.dir.mkdir(parents=True, exist_ok=True)
            proj_a = Path(tmp) / "projA"
            proj_a.mkdir()
            ws_a = host.update_workspace(
                host.create("a").id,
                workdir=str(proj_a),
                workdir_lock=True,
            )
            ws_b = host.create("b")
            env_a = ws_a.env({})
            env_b = ws_b.env({})
            self.assertEqual(env_a["AGENTS_WORKDIR"], str(proj_a.resolve()))
            self.assertEqual(env_a["AGENTS_WORKDIR_LOCK"], "1")
            self.assertEqual(env_b["AGENTS_WORKDIR"], str(ws_b.dir / "work"))
            self.assertNotIn("AGENTS_WORKDIR_LOCK", env_b)
            self.assertNotEqual(env_a["AGENTS_WORKDIR"], env_b["AGENTS_WORKDIR"])

    def test_explicit_env_workdir_beats_workspace_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.dir.mkdir(parents=True, exist_ok=True)
            ws = host.update_workspace(
                host.create("a").id, workdir=str(Path(tmp) / "projA")
            )
            env = ws.env({"AGENTS_WORKDIR": "/tmp/override"})
            self.assertEqual(env["AGENTS_WORKDIR"], str(_p("/tmp/override")))

    def test_env_workdir_param_beats_workspace_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.dir.mkdir(parents=True, exist_ok=True)
            ws = host.update_workspace(
                host.create("a").id, workdir=str(Path(tmp) / "projA")
            )
            env = ws.env({}, workdir="/tmp/param")
            self.assertEqual(env["AGENTS_WORKDIR"], str(_p("/tmp/param")))

    def test_update_workspace_validations(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.dir.mkdir(parents=True, exist_ok=True)
            host.create("a")
            with self.assertRaises(ValueError):
                host.update_workspace("ghost", label="x")
            with self.assertRaises(ValueError):
                host.update_workspace("a", workdir=str(host.secrets_dir))
            with self.assertRaises(ValueError):
                host.update_workspace("a", workdir=str(host.keys / "k"))
            with self.assertRaises(ValueError):
                host.update_workspace("a", workdir="/tmp/x\x00y")
            ws = host.update_workspace("a", label="  New Label  ")
            self.assertEqual(ws.label, "New Label")
            ws = host.update_workspace("a", workdir=None, workdir_lock=False)
            import json

            data = json.loads(ws.workspace_file.read_text(encoding="utf-8"))
            self.assertNotIn("workdir", data)
            self.assertNotIn("workdir_lock", data)
            self.assertEqual(data["label"], "New Label")

    def test_dangerous_stored_workdir_ignored_with_warning(self):
        import json
        import warnings

        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.dir.mkdir(parents=True, exist_ok=True)
            ws = host.create("a")
            data = json.loads(ws.workspace_file.read_text(encoding="utf-8"))
            data["workdir"] = str(host.secrets_dir)
            ws.workspace_file.write_text(json.dumps(data), encoding="utf-8")
            with warnings.catch_warnings(record=True) as seen:
                warnings.simplefilter("always")
                env = ws.env({})
            self.assertEqual(env["AGENTS_WORKDIR"], str(ws.dir / "work"))
            self.assertTrue(any(w.category is UserWarning for w in seen))

    def test_caller_workdir_lock_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            ws = host.update_workspace(
                host.create("a").id, workdir_lock=True
            )
            env = ws.env({"AGENTS_WORKDIR_LOCK": "0"})
            self.assertEqual(env["AGENTS_WORKDIR_LOCK"], "0")


class TestWorkspaceConfig(unittest.TestCase):
    def test_read_missing_and_invalid(self):
        import json

        from agents_workspace import read_workspace_config

        with tempfile.TemporaryDirectory() as tmp:
            ws = Host.open(tmp).create("a")
            self.assertEqual(read_workspace_config(ws), {})
            ws.config.write_text("{bad", encoding="utf-8")
            self.assertEqual(read_workspace_config(ws), {})
            ws.config.write_text(json.dumps([1, 2]), encoding="utf-8")
            with self.assertRaises(ValueError):
                read_workspace_config(ws)

    def test_update_approval_mode_roundtrip_and_validation(self):
        from agents_workspace import read_workspace_config, update_workspace_config

        with tempfile.TemporaryDirectory() as tmp:
            ws = Host.open(tmp).create("a")
            out = update_workspace_config(ws, {"approval": {"mode": "strict"}})
            self.assertEqual(out["approval"]["mode"], "strict")
            self.assertEqual(read_workspace_config(ws)["approval"]["mode"], "strict")
            with self.assertRaises(ValueError):
                update_workspace_config(ws, {"approval": {"mode": "yolo"}})
            # clearing with empty value
            out = update_workspace_config(ws, {"approval": {"mode": ""}})
            self.assertNotIn("mode", out["approval"])

    def test_update_rejects_secret_keys(self):
        from agents_workspace import update_workspace_config

        with tempfile.TemporaryDirectory() as tmp:
            ws = Host.open(tmp).create("a")
            for bad in (
                {"RELAY_SECRET": "x"},
                {"OPENROUTER_API_KEY": "x"},
                {"nested": {"MY_TOKEN": "x"}},
            ):
                with self.assertRaises(ValueError, msg=str(bad)):
                    update_workspace_config(ws, bad)
            self.assertFalse(ws.config.exists() and "RELAY_SECRET" in ws.config.read_text())

    def test_update_deep_merges(self):
        from agents_workspace import update_workspace_config

        with tempfile.TemporaryDirectory() as tmp:
            ws = Host.open(tmp).create("a")
            update_workspace_config(ws, {"model": {"provider": "openai"}})
            out = update_workspace_config(ws, {"model": {"name": "gpt-5"}})
            self.assertEqual(out["model"], {"provider": "openai", "name": "gpt-5"})


class TestWorkspaceStoreContract(unittest.TestCase):
    def test_store_db_isolated_per_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            ws_a = host.create("a")
            ws_b = host.create("b")
            self.assertEqual(ws_a.store_db, ws_a.dir / "store.db")
            self.assertEqual(ws_b.store_db, ws_b.dir / "store.db")
            self.assertNotEqual(ws_a.store_db, ws_b.store_db)
            self.assertEqual(ws_a.store_exports, ws_a.dir / "exports")

    def test_env_sets_and_scrubs_store_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            ws_a = host.create("a")
            ws_b = host.create("b")
            env_a = ws_a.env({})
            self.assertEqual(env_a["AGENTS_STORE_DB"], str(ws_a.store_db))
            # Switching workspaces never leaks workspace A's store path.
            env_b = ws_b.env(env_a)
            self.assertEqual(env_b["AGENTS_STORE_DB"], str(ws_b.store_db))
            self.assertNotEqual(env_b["AGENTS_STORE_DB"], env_a["AGENTS_STORE_DB"])


class TestWorkspaceSyncPolicy(unittest.TestCase):
    """agents-workspace#43 ownership fields: sync_policy, sync_hosts,
    default_host, repos, execution — round-trip + validation."""

    def test_defaults_on_fresh_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            ws = host.create("a")
            self.assertEqual(ws.sync_policy, "synced")
            self.assertEqual(ws.sync_hosts, ())
            self.assertEqual(ws.default_host, "")
            self.assertEqual(ws.repos, ())
            self.assertEqual(ws.execution, {})

    def test_round_trip_all_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("a")
            ws = host.update_workspace(
                "a",
                sync_policy="local",
                sync_hosts=["dev-1", "dev-2"],
                default_host="dev-1",
                repos=[{"slug": "org/app", "url": "git@x:org/app",
                        "checkout_mode": "worktree"}],
                execution={"failover": "standby", "lease_sec": 120},
            )
            self.assertEqual(ws.sync_policy, "local")
            self.assertEqual(ws.sync_hosts, ("dev-1", "dev-2"))
            self.assertEqual(ws.default_host, "dev-1")
            self.assertEqual(ws.repos[0]["slug"], "org/app")
            self.assertEqual(ws.repos[0]["checkout_mode"], "worktree")
            self.assertEqual(ws.execution["failover"], "standby")
            self.assertEqual(ws.execution["lease_sec"], 120)
            # re-read from disk
            ws2 = host.workspace("a")
            self.assertEqual(ws2.sync_policy, "local")
            self.assertEqual(ws2.sync_hosts, ("dev-1", "dev-2"))

    def test_validation_rejects_unknown_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("a")
            with self.assertRaises(ValueError):
                host.update_workspace("a", sync_policy="everything")
            with self.assertRaises(ValueError):
                host.update_workspace(
                    "a", repos=[{"slug": "x", "checkout_mode": "mirror"}])
            with self.assertRaises(ValueError):
                host.update_workspace("a", repos=[{"no_slug": 1}])
            with self.assertRaises(ValueError):
                host.update_workspace("a", execution={"failover": "chaos"})

    def test_clear_fields_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("a")
            host.update_workspace("a", sync_policy="local",
                                  default_host="d1")
            ws = host.update_workspace("a", sync_policy="synced",
                                       default_host=None,
                                       sync_hosts=[], repos=[],
                                       execution=None)
            self.assertEqual(ws.sync_policy, "synced")
            self.assertEqual(ws.default_host, "")
            self.assertEqual(ws.sync_hosts, ())
            self.assertEqual(ws.repos, ())
            self.assertEqual(ws.execution, {})


class TestWorkspaceSyncHostsValidation(unittest.TestCase):
    """sync_hosts = hub device-id allowlist: strict on write, tolerant on read."""

    def test_rejects_non_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("a")
            for bad in ("dev-1", b"dev-1", {"dev-1": True}, 5):
                with self.assertRaises(ValueError):
                    host.update_workspace("a", sync_hosts=bad)

    def test_rejects_bad_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("a")
            for bad in ([1], ["ok", None], [""], ["has space"], ["../x"],
                        ["x" * 200]):
                with self.assertRaises(ValueError):
                    host.update_workspace("a", sync_hosts=bad)

    def test_strips_and_dedupes(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            host.create("a")
            ws = host.update_workspace(
                "a", sync_hosts=[" d1 ", "d1", "a1b2c3d4e5f60718"])
            self.assertEqual(ws.sync_hosts, ("d1", "a1b2c3d4e5f60718"))

    def test_hand_edited_bad_value_does_not_crash_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Host.open(tmp)
            ws = host.create("a")
            meta = json.loads(ws.workspace_file.read_text(encoding="utf-8"))
            meta["sync_hosts"] = "dev-1"
            ws.workspace_file.write_text(json.dumps(meta), encoding="utf-8")
            self.assertEqual(host.workspace("a").sync_hosts, ())
            meta["sync_hosts"] = ["ok-1", 7, "bad id"]
            ws.workspace_file.write_text(json.dumps(meta), encoding="utf-8")
            self.assertEqual(host.workspace("a").sync_hosts, ("ok-1",))
