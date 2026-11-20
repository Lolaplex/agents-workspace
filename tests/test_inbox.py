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
    reply_task,
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

    def test_hop_limit_refuses(self):
        task = create_task(self.ws, "default", "x", max_hops=1)
        handoff(self.ws, task["id"], "cos")
        self.assertEqual(task_owner(self.ws, task["id"]), "cos")
        with self.assertRaises(ValueError):
            handoff(self.ws, task["id"], "default")
        self.assertEqual(task_owner(self.ws, task["id"]), "cos")
        self.assertEqual(find_task(self.ws, task["id"])[1]["hop"], 1)

    def test_bad_mode_status_hop_rejected(self):
        with self.assertRaises(ValueError):
            create_task(self.ws, "default", "x", mode="yell")
        with self.assertRaises(ValueError):
            create_task(self.ws, "default", "x", status="maybe")
        with self.assertRaises(ValueError):
            create_task(self.ws, "default", "x", hop=5, max_hops=2)
        with self.assertRaises(ValueError):
            create_task(self.ws, "default", "x", thread_id="bad id")

    def test_accepts_messages_false_refuses(self):
        (self.ws.personas / "cos" / "persona.json").write_text(
            '{"id":"cos","accepts_messages":false}', encoding="utf-8")
        task = create_task(self.ws, "default", "x")
        with self.assertRaises(ValueError):
            handoff(self.ws, task["id"], "cos")
        self.assertEqual(task_owner(self.ws, task["id"]), "default")
        with self.assertRaises(ValueError):
            create_task(self.ws, "cos", "ping", from_persona="default",
                        mode="ask")

    def test_allow_peers_restricts_senders(self):
        (self.ws.personas / "cos" / "persona.json").write_text(
            '{"id":"cos","allow_peers":["default"]}', encoding="utf-8")
        (self.ws.personas / "ops").mkdir(parents=True)
        (self.ws.personas / "ops" / "persona.json").write_text(
            '{"id":"ops"}', encoding="utf-8")
        task = create_task(self.ws, "default", "x")
        handoff(self.ws, task["id"], "cos")  # default is allowed
        # cos -> default fine (default has no allow_peers = all)
        handoff(self.ws, task["id"], "default")
        # ops is not in cos's allow_peers
        handoff(self.ws, task["id"], "ops")
        with self.assertRaises(ValueError):
            handoff(self.ws, task["id"], "cos")

    def test_ask_reply_roundtrip(self):
        task = create_task(self.ws, "cos", "what is 2+2",
                           from_persona="default", mode="ask",
                           thread_id="thr_q1")
        self.assertEqual(task["to_persona"], "")
        self.assertEqual(task["mode"], "ask")
        self.assertEqual(task["status"], "open")
        self.assertEqual(task["thread_id"], "thr_q1")
        answered = reply_task(self.ws, task["id"], result="4",
                              reply_summary="math", status="done")
        self.assertEqual(answered["owner"], "default")
        self.assertEqual(answered["status"], "done")
        self.assertEqual(answered["result"], "4")
        self.assertEqual(answered["hop"], 1)
        self.assertEqual(task_owner(self.ws, task["id"]), "default")
        # thread survives the round trip
        self.assertEqual(answered["thread_id"], "thr_q1")

    def test_reply_without_sender_updates_in_place(self):
        task = create_task(self.ws, "default", "x")
        out = reply_task(self.ws, task["id"], status="failed",
                         result="nope")
        self.assertEqual(out["owner"], "default")
        self.assertEqual(out["status"], "failed")
        self.assertEqual(task_owner(self.ws, task["id"]), "default")

    def test_thread_id_minted_and_kept(self):
        task = create_task(self.ws, "default", "x")
        self.assertTrue(task["thread_id"].startswith("thr_"))
        moved = handoff(self.ws, task["id"], "cos")
        self.assertEqual(moved["thread_id"], task["thread_id"])
        self.assertEqual(moved["from_persona"], "default")
        self.assertEqual(moved["to_persona"], "cos")

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
