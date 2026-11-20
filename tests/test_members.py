"""Workspace member registry <ws>/members.json (D-members)."""
import json
import tempfile
import unittest
from pathlib import Path

from agents_workspace import Host
from agents_workspace.members import (
    add_member,
    member_role,
    migrate_members,
    read_members,
    remove_member,
    write_members,
)


class Members(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.host = Host(dir=Path(self._tmp.name) / "host")
        self.ws = self.host.create("demo")

    def tearDown(self):
        self._tmp.cleanup()

    def test_roundtrip(self):
        write_members(self.ws, [
            {"id": "u_felix", "role": "owner", "did": "did:key:z1"},
            {"id": "u_agent", "role": "member"},
        ])
        rows = read_members(self.ws)
        self.assertEqual(rows[0]["id"], "u_felix")
        self.assertEqual(rows[0]["role"], "owner")
        self.assertEqual(rows[0]["did"], "did:key:z1")
        self.assertNotIn("did", rows[1])
        self.assertTrue(self.ws.members.is_file())

    def test_ids_and_roles_validated(self):
        with self.assertRaises(ValueError):
            write_members(self.ws, [{"id": "../evil", "role": "owner"}])
        with self.assertRaises(ValueError):
            write_members(self.ws, [{"id": "u_x", "role": ""}])
        with self.assertRaises(ValueError):
            add_member(self.ws, "bad id!", role="member")

    def test_add_remove_and_role_lookup(self):
        add_member(self.ws, "u_felix", role="owner", did="did:key:z1")
        add_member(self.ws, "u_agent")
        self.assertEqual(member_role(self.ws, "u_felix"), "owner")
        self.assertEqual(member_role(self.ws, "u_agent"), "member")
        self.assertEqual(member_role(self.ws, "u_nobody"), "")
        # upsert replaces the row
        add_member(self.ws, "u_agent", role="owner")
        self.assertEqual(member_role(self.ws, "u_agent"), "owner")
        self.assertEqual(len(read_members(self.ws)), 2)
        self.assertTrue(remove_member(self.ws, "u_agent"))
        self.assertFalse(remove_member(self.ws, "u_agent"))

    def test_migrate_maps_users_first_owner(self):
        users = {
            "users": {
                "u_felix": {"id": "u_felix", "aliases": ["repl:local"],
                            "did": "did:key:zF"},
                "u_agent": {"id": "u_agent", "aliases": ["http:bot"]},
            }
        }
        users_file = self.ws.dir / "users.json"
        users_file.write_text(json.dumps(users), encoding="utf-8")
        rows = migrate_members(self.ws, users_file)
        self.assertEqual(
            [(m["id"], m["role"]) for m in rows],
            [("u_felix", "owner"), ("u_agent", "member")],
        )
        self.assertEqual(rows[0].get("did"), "did:key:zF")
        # idempotent: no dup, no role change on re-run
        rows2 = migrate_members(self.ws, users_file)
        self.assertEqual(rows, rows2)

    def test_migrate_keeps_existing_owner(self):
        add_member(self.ws, "u_boss", role="owner")
        users = {"users": {"u_new": {"id": "u_new"}}}
        users_file = self.ws.dir / "users.json"
        users_file.write_text(json.dumps(users), encoding="utf-8")
        rows = migrate_members(self.ws, users_file)
        roles = {m["id"]: m["role"] for m in rows}
        self.assertEqual(roles, {"u_boss": "owner", "u_new": "member"})

    def test_migrate_without_users_file(self):
        rows = migrate_members(
            self.ws, self.ws.dir / "missing.json")
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
