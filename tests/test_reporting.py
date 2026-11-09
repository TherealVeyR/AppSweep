"""JSON and CSV export tests."""

from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from appssweep.analyzer import build_report
from appssweep.categories import CleanupDiscovery
from appssweep.models import ActionKind, CleanupItem, RiskGroup, ScanConfig, ScanResult
from appssweep.reporting import (
    ExportError,
    file_rows_for_csv,
    render_csv,
    render_json,
    report_to_dict,
    write_export,
)

from .helpers import snapshot_for, write_file


class ReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.file = write_file(self.root / "video.mp4", 1536)
        self.snapshot = snapshot_for(self.file)
        self.item = CleanupItem(
            key="cache:" + str(self.root / ".cache" / "app"),
            title="Test cache",
            group=RiskGroup.SAFE,
            size_bytes=1024,
            summary="A cache.",
            description="A test cache folder.",
            consequences="It can be rebuilt.",
            method="Clear the folder contents.",
            action=ActionKind.CLEAR_CACHE_CONTENTS,
            paths=(str(self.root / ".cache" / "app"),),
            details=("1 file",),
        )
        self.scan = ScanResult(
            config=ScanConfig(
                mode="custom",
                roots=(str(self.root),),
                min_file_size=0,
                personal_roots=(str(self.root),),
            ).normalized(),
            roots=(str(self.root),),
            total_files=1,
            unique_files=1,
            total_bytes=self.snapshot.size,
            top_files=[self.snapshot],
        )
        self.discovery = CleanupDiscovery(
            items=[self.item],
            categories_run=["cache"],
            notes=["Cache note."],
        )
        self.analysis = build_report(
            self.scan,
            self.discovery,
            scan_started="2026-01-01T00:00:00+00:00",
            app_version="1.0.0",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_json_contains_scan_files_and_cleanup_items(self) -> None:
        payload = json.loads(
            render_json(self.analysis.report, self.analysis, self.discovery)
        )
        self.assertEqual(payload["app"], "AppSweep")
        self.assertEqual(payload["personal_files"][0]["path"], str(self.file))
        self.assertEqual(payload["personal_files"][0]["size"], 1536)
        self.assertEqual(payload["cleanup_items"][0]["key"], self.item.key)
        self.assertEqual(payload["summary"]["confirmed_cleanup_bytes"], 1024)
        self.assertEqual(payload["categories_run"], ["cache"])
        self.assertEqual(payload["report_only_findings"], [])

    def test_csv_has_both_record_types(self) -> None:
        text = render_csv(self.analysis.report, self.analysis, self.discovery)
        rows = list(csv.DictReader(io.StringIO(text)))
        record_types = [row["record_type"] for row in rows]
        self.assertIn("personal_file", record_types)
        self.assertIn("cleanup_item", record_types)
        personal = next(row for row in rows if row["record_type"] == "personal_file")
        self.assertEqual(personal["path"], str(self.file))
        self.assertEqual(personal["size_bytes"], "1536")
        cleanup = next(row for row in rows if row["record_type"] == "cleanup_item")
        self.assertEqual(cleanup["eligible"], "yes")
        self.assertEqual(cleanup["group"], "safe")

    def test_csv_neutralizes_spreadsheet_formulas(self) -> None:
        malicious = write_file(self.root / "=2+2.bin", 10)
        self.analysis.report.personal_files = [snapshot_for(malicious)]
        self.item.title = "@SUM(1+1)"
        self.item.description = "  =cmd|' /C calc'!A0"
        self.item.consequences = "-2+3"
        self.item.method = "\t=malicious"
        rows = list(
            csv.DictReader(
                io.StringIO(
                    render_csv(self.analysis.report, self.analysis, self.discovery)
                )
            )
        )
        personal = next(row for row in rows if row["record_type"] == "personal_file")
        cleanup = next(row for row in rows if row["record_type"] == "cleanup_item")
        self.assertTrue(personal["filename"].startswith("'="))
        self.assertTrue(cleanup["title"].startswith("'@"))
        self.assertTrue(cleanup["description"].startswith("'  ="))
        self.assertTrue(cleanup["consequences"].startswith("'-"))
        self.assertTrue(cleanup["method"].startswith("'\t"))
        self.assertEqual(cleanup["size_bytes"], "1024")

    def test_report_to_dict_preserves_disabled_item_reason(self) -> None:
        self.item.eligible = False
        self.item.report_only_reason = "Overlaps another item."
        payload = report_to_dict(self.analysis.report, self.analysis, self.discovery)
        self.assertFalse(payload["cleanup_items"][0]["eligible"])
        self.assertEqual(
            payload["cleanup_items"][0]["report_only_reason"],
            "Overlaps another item.",
        )

    def test_write_export_is_atomic_and_private(self) -> None:
        destination = self.root / "nested" / "report.json"
        write_export(str(destination), render_json(self.analysis.report))
        self.assertTrue(destination.exists())
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        self.assertIn("AppSweep", destination.read_text(encoding="utf-8"))

    def test_write_export_rejects_stdout_marker(self) -> None:
        with self.assertRaises(ExportError):
            write_export("-", "{}")

    def test_file_rows_helper(self) -> None:
        rows = file_rows_for_csv([self.snapshot])
        self.assertEqual(rows[0]["number"], 1)
        self.assertEqual(rows[0]["path"], str(self.file))


if __name__ == "__main__":
    unittest.main()
