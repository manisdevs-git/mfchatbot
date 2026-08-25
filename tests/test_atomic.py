"""Atomic directory swap used by corpus refresh and index rebuild."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ingest.atomic import replace_directory


class ReplaceDirectoryTests(unittest.TestCase):
    def test_incoming_replaces_live_and_drops_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "processed"
            incoming = Path(tmp) / "processed.next"
            live.mkdir()
            incoming.mkdir()
            (live / "old.txt").write_text("old\n", encoding="utf-8")
            (incoming / "chunks.jsonl").write_text("new\n", encoding="utf-8")

            replace_directory(live, incoming)

            self.assertEqual((live / "chunks.jsonl").read_text(encoding="utf-8"), "new\n")
            self.assertFalse((live / "old.txt").exists())
            self.assertFalse(incoming.exists())
            self.assertFalse((Path(tmp) / "processed.bak").exists())

    def test_missing_incoming_leaves_live_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "processed"
            live.mkdir()
            (live / "old.txt").write_text("old\n", encoding="utf-8")
            with self.assertRaises(FileNotFoundError):
                replace_directory(live, Path(tmp) / "missing")
            self.assertEqual((live / "old.txt").read_text(encoding="utf-8"), "old\n")


if __name__ == "__main__":
    unittest.main()
