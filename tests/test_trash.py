"""Mocked Trash discovery preview and no-fallback behavior tests.

No real host Trash is moved or emptied.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from appssweep.trash_manager import discover_trash
from appssweep.util import human_bytes
from tests.helpers import FakeRunner, write_file


class TrashTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / "share"
        self.files = self.data / "Trash" / "files"
        self.files.mkdir(parents=True)
        write_file(self.files / "old.bin", 1024)
        write_file(self.files / "report.txt", 2048)
        self.env_patch = patch.dict(
            os.environ,
            {"XDG_DATA_HOME": str(self.data)},
        )
        self.env_patch.start()

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.tmp.cleanup()

    def test_trash_preview_shows_size_count_and_example_contents(self) -> None:
        runner = FakeRunner(mapping={"gio": "/usr/bin/gio"})

        discovery = discover_trash(runner)

        self.assertEqual(discovery.total_size, 3072)
        self.assertEqual(discovery.file_count, 2)
        self.assertEqual(len(discovery.items), 1)
        item = discovery.items[0]
        self.assertEqual(item.key, "user-trash")
        self.assertIn(human_bytes(discovery.total_size), item.description)
        self.assertIn("Top-level items: 2", item.details)
        examples = next(detail for detail in item.details if detail.startswith("Examples:"))
        self.assertIn("old.bin", examples)
        self.assertIn("report.txt", examples)
        self.assertEqual(
            set(item.metadata["example_names"]), {"old.bin", "report.txt"}
        )

    def test_trash_is_report_only_without_gio(self) -> None:
        discovery = discover_trash(FakeRunner())

        self.assertEqual(discovery.items, [])
        self.assertTrue(discovery.report_only)
        self.assertIn("gio is not installed", discovery.report_only[0].reason)


if __name__ == "__main__":
    unittest.main()
