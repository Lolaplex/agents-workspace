"""Persona inbox + explicit handoff ownership (P2, klanker#18)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agents_workspace.inbox as inbox_mod
from agents_workspace import Host
from agents_workspace.inbox import (
    create_task,
    find_task,
    handoff,
    list_inbox,
    task_owner,
)


class PersonaInbox(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.host = Host(dir=Path(self._tmp.name) / "host")
        self.ws = self.host.create("demo")
        # second equal persona
        (self.ws.personas / "cos").mkdir(parents=True)
        (self.ws.personas / "cos" / "persona.json").write_text(
            '{"id":"cos"}', encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_handoff_moves_ownership(self):
        task = create_task(self.ws, "default", "write report", user="felix")
        self.assertEqual(task_owner(self.ws, task["id"]), "default")
        moved = handoff(self.ws, task["id"], "cos", note="yours now")
        self.assertEqual(moved["owner"], "cos")
        self.assertEqual(task_owner(self.ws, task["id"]), "cos")
        inbox_cos = list_inbox(self.ws, "cos")
        self.assertEqual([t["id"] for t in inbox_cos], [task["id"]])
        self.assertEqual(list_inbox(self.ws, "default"), [])
        events = moved["events"]
        self.assertEqual(events[-1]["event"], "handoff")
        self.assertEqual(events[-1]["from"], "default")
        self.assertEqual(events[-1]["to"], "cos")

    def test_from_mismatch_refuses(self):
        task = create_task(self.ws, "default", "x")
        with self.assertRaises(ValueError):
            handoff(self.ws, task["id"], "cos", from_persona="cos")
        self.assertEqual(task_owner(self.ws, task["id"]), "default")

    def test_no_default_privilege(self):
        # 'default' has no routing privilege: a task for cos never
        # detours through default's inbox.
        task = create_task(self.ws, "cos", "direct")
        self.assertEqual(task_owner(self.ws, task["id"]), "cos")
        self.assertEqual(list_inbox(self.ws, "default"), [])

    def test_ids_validated(self):
        with self.assertRaises(ValueError):
            create_task(self.ws, "../evil", "x")
        with self.assertRaises(ValueError):
            handoff(self.ws, "tsk_x", "/tmp/p")

    def test_find_task_unknown(self):
        self.assertIsNone(find_task(self.ws, "tsk_nope12345"))

    def test_handoff_crash_never_dups(self):
        # agents-workspace#14: a crash mid-handoff must leave the task in
        # exactly one inbox — owner = location admits no duplicates.
        task = create_task(self.ws, "default", "x")
        tid = task["id"]

        def inboxes():
            return [p.name for p in self.ws.personas.iterdir()
                    if (p / "inbox" / f"{tid}.json").is_file()]

        # crash right after the destination write (pre-fix this left the
        # task in both inboxes)
        real_save = inbox_mod._save
        def boom(path, t):
            real_save(path, t)
            raise RuntimeError("crash")
        with patch.object(inbox_mod, "_save", boom):
            with self.assertRaises(RuntimeError):
                handoff(self.ws, tid, "cos")
        self.assertEqual(len(inboxes()), 1)

        # crash mid-move: still exactly one owner
        real_replace = os.replace
        calls = []
        def boom2(a, b):
            calls.append(b)
            if len(calls) == 2:
                raise RuntimeError("crash")
            return real_replace(a, b)
        with patch.object(os, "replace", boom2):
            with self.assertRaises(RuntimeError):
                handoff(self.ws, tid, "cos")
        self.assertEqual(len(inboxes()), 1)

        # a clean retry completes the handoff
        handoff(self.ws, tid, "cos")
        self.assertEqual(task_owner(self.ws, tid), "cos")
        self.assertEqual(inboxes(), ["cos"])


if __name__ == "__main__":
    unittest.main()
