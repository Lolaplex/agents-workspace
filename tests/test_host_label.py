"""Tests for Host.host_label() and Host.set_host_label()."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from agents_workspace import Host


class TestHostLabel(unittest.TestCase):
    def test_default_falls_back_to_hostname_never_raw_host_id(self) -> None:
        with TemporaryDirectory() as td:
            h = Host(Path(td))
            with patch("agents_workspace.workspace.socket.gethostname", return_value="Felix-Desktop"):
                lbl = h.host_label()
            self.assertEqual(lbl, "Felix-Desktop")
            self.assertNotEqual(lbl, h.host_id())

    def test_loopback_hostname_falls_back_to_this_host(self) -> None:
        with TemporaryDirectory() as td:
            h = Host(Path(td))
            for bad in ("localhost", "127.0.0.1", ""):
                with patch("agents_workspace.workspace.socket.gethostname", return_value=bad):
                    self.assertEqual(h.host_label(), "this-host")

    def test_set_host_label_sanitizes_caps_and_persists(self) -> None:
        with TemporaryDirectory() as td:
            h = Host(Path(td))
            saved = h.set_host_label("  Felix\nWork\tPC\x07  ")
            self.assertEqual(saved, "Felix Work PC")
            self.assertEqual(h.host_label(), "Felix Work PC")
            raw = json.loads(h.agents_json.read_text(encoding="utf-8"))
            self.assertEqual(raw["host_label"], "Felix Work PC")

            long_saved = h.set_host_label("x" * 100)
            self.assertEqual(len(long_saved), 64)

            with self.assertRaises(ValueError):
                h.set_host_label("   \n\x00  ")
