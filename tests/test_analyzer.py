"""Analyzer overlap, de-duplication, and report-only tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from appsweep.analyzer import all_report_only, build_report
from appsweep.categories import CleanupDiscovery
from appsweep.models import (
    ActionKind,
    CleanupItem,
    ReportOnlyFinding,
    RiskGroup,
    ScanConfig,
    ScanResult,
)

from .helpers import snapshot_for, write_file


def cleanup_item(
    key: str,
    path: Path,
    *,
    size: int = 1024,
    action: ActionKind = ActionKind.CLEAR_CACHE_CONTENTS,
    eligible: bool = True,
    reason: str = "",
) -> CleanupItem:
    return CleanupItem(
        key=key,
        title=f"Item {key}",
        group=RiskGroup.SAFE,
        size_bytes=size,
        summary="Test item.",
        description="A test cleanup item.",
        consequences="Test consequence.",
        method="Test method.",
        action=action,
        paths=(str(path),),
        eligible=eligible,
        report_only_reason=reason,
    )


class AnalyzerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.scan = ScanResult(
            config=ScanConfig(
                mode="custom",
                roots=(str(self.root),),
                min_file_size=0,
                top_n=50,
                personal_roots=(str(self.root),),
            ).normalized(),
            roots=(str(self.root),),
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_overlapping_cleanup_items_are_disabled_directly_by_key(self) -> None:
        cache = self.root / "cache"
        nested = cache / "app"
        nested.mkdir(parents=True)
        first = cleanup_item("cache]first", cache, size=2048)
        second = cleanup_item("cache]second", nested, size=1024)
        discovery = CleanupDiscovery(items=[first, second])

        analysis = build_report(self.scan, discovery)

        self.assertFalse(first.eligible)
        self.assertFalse(second.eligible)
        self.assertTrue(first.report_only_reason)
        self.assertTrue(second.report_only_reason)
        self.assertTrue(any("Cleanup overlap" in warning for warning in analysis.warnings))
        self.assertIn(
            "Overlapping cleanup items were disabled rather than added together.",
            analysis.report.notes,
        )

    def test_personal_file_inside_cleanup_path_is_removed_from_top_files(self) -> None:
        documents = self.root / "Documents"
        personal = write_file(documents / "large.bin", 4096)
        self.scan.top_files = [snapshot_for(personal)]
        item = cleanup_item("cache:documents", documents, size=4096)
        discovery = CleanupDiscovery(items=[item])

        analysis = build_report(self.scan, discovery)

        self.assertEqual(analysis.report.personal_files, [])
        self.assertTrue(
            any("overlaps cleanup item" in warning for warning in analysis.warnings)
        )
        self.assertIn(
            "Personal files inside selected cleanup-category paths were left out of "
            "the large-file list; the category already accounts for that space.",
            analysis.report.notes,
        )

    def test_report_only_merge_includes_disabled_items_sorted_by_size(self) -> None:
        disabled = cleanup_item(
            "disabled",
            self.root,
            size=900,
            action=ActionKind.REPORT_ONLY,
            eligible=False,
            reason="Needs a manual decision.",
        )
        finding = ReportOnlyFinding(
            title="Manual finding",
            size_bytes=500,
            detail="A report-only finding.",
            reason="Not automated.",
            group=RiskGroup.SAFE,
        )
        discovery = CleanupDiscovery(
            items=[disabled],
            report_only=[finding],
        )

        analysis = build_report(self.scan, discovery)
        findings = all_report_only(discovery, analysis)

        self.assertEqual([item.title for item in findings], ["Item disabled", "Manual finding"])
        self.assertEqual(findings[0].reason, "Needs a manual decision.")


if __name__ == "__main__":
    unittest.main()
