"""Presentation tests: displayed ranking must match the selection map."""

from __future__ import annotations

import io
import re
import tempfile
import unittest
from pathlib import Path

from appssweep.analyzer import build_report
from appssweep.categories import CleanupDiscovery
from appssweep.large_files import numbered_personal_map, personal_file_rows
from appssweep.models import ScanConfig, ScanResult
from appssweep.ui import UI, supports_rich

from .helpers import snapshot_for, write_file


class FileNumberingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "scan"
        self.root.mkdir()

        snapshots = []
        for index in range(55):
            path = write_file(self.root / f"file-{index:02d}.bin", (index + 1) * 100)
            snapshots.append(snapshot_for(path))
        # Deliberately unsorted: the report, display, and selection must all
        # derive the same ranking rather than trusting scan insertion order.
        snapshots.reverse()

        self.scan = ScanResult(
            config=ScanConfig(
                mode="custom",
                roots=(str(self.root),),
                min_file_size=0,
                top_n=50,
                personal_roots=(str(self.root),),
            ).normalized(),
            roots=(str(self.root),),
            total_files=len(snapshots),
            unique_files=len(snapshots),
            total_bytes=sum(file.size for file in snapshots),
            top_files=snapshots,
        )
        self.report = build_report(self.scan, CleanupDiscovery()).report

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_top_fifty_display_matches_selection_map(self) -> None:
        expected = personal_file_rows(self.scan, limit=50)
        self.assertEqual(len(expected), 50)
        self.assertEqual(self.report.personal_files, expected)

        mapping = numbered_personal_map(self.scan)
        self.assertEqual(len(mapping), 50)
        self.assertEqual(list(mapping.values()), self.report.personal_files)
        self.assertGreaterEqual(expected[0].size, expected[-1].size)

        output = io.StringIO()
        UI(stream=output, color=False, width=100).files(self.report.personal_files)
        text = output.getvalue()

        displayed_paths = [
            line.split("Path:", 1)[1].strip()
            for line in text.splitlines()
            if "Path:" in line
        ]
        self.assertEqual(displayed_paths, [file.path for file in expected])

        ranks = [
            int(match.group(1))
            for line in text.splitlines()
            if (match := re.match(r"^\s+(\d+)\s{2,}", line))
        ]
        self.assertEqual(ranks, list(range(1, 51)))

    @unittest.skipUnless(supports_rich(), "Rich is not installed")
    def test_rich_rendering_preserves_the_same_file_list(self) -> None:
        output = io.StringIO()
        UI(stream=output, color=True, width=100).files(self.report.personal_files)
        text = output.getvalue()
        self.assertEqual(text.count("Path:"), 50)
        self.assertIn(self.report.personal_files[0].filename, text)
        self.assertIn(self.report.personal_files[-1].filename, text)


if __name__ == "__main__":
    unittest.main()
