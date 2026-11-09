"""Report assembly and storage calculations.

The analyzer never scans the filesystem and never executes cleanup commands.
It turns scanner data plus cleanup discovery into transparent totals.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from .categories import CleanupDiscovery
from .large_files import personal_file_rows
from .models import (
    CleanupItem,
    CleanupReport,
    FileSnapshot,
    ReportOnlyFinding,
    RiskGroup,
    ScanResult,
)
from .util import is_within, now_iso


@dataclass
class Analysis:
    report: CleanupReport
    warnings: list[str] = field(default_factory=list)
    overlaps: list[str] = field(default_factory=list)

    @property
    def confirmed_cleanup_total(self) -> int:
        return sum(
            item.size_bytes
            for item in self.report.eligible_cleanup_items
            if not item.estimated
        )

    @property
    def estimated_cleanup_total(self) -> int:
        return sum(
            item.size_bytes
            for item in self.report.eligible_cleanup_items
            if item.estimated
        )

    @property
    def cleanup_total(self) -> int:
        return self.confirmed_cleanup_total + self.estimated_cleanup_total

    @property
    def personal_total(self) -> int:
        return sum(file.size for file in self.report.personal_files)

    @property
    def largest_personal_total(self) -> int:
        return self.personal_total


def build_report(
    scan: ScanResult,
    discovery: CleanupDiscovery,
    *,
    scan_started: str = "",
    app_version: str = "",
    extra_notes: Iterable[str] = (),
) -> Analysis:
    personal_files = personal_file_rows(scan, limit=scan.config.top_n)
    items = list(discovery.items)
    warnings: list[str] = []
    overlaps, overlapping_keys = _find_cleanup_overlaps(items)
    if overlaps:
        warnings.extend(overlaps)
        for item in items:
            if item.key in overlapping_keys:
                item.eligible = False
                item.report_only_reason = (
                    "Overlapping cleanup paths were detected; this item is disabled "
                    "to avoid double-counting or removing the same data twice."
                )
        warnings.append(
            "Overlapping cleanup items were disabled rather than added together."
        )

    personal_overlaps = _find_personal_category_overlaps(items, personal_files)
    if personal_overlaps:
        warnings.extend(personal_overlaps)
        personal_files = [
            file
            for file in personal_files
            if not any(
                _paths_overlap(file.path, path)
                for item in items
                for path in item.paths
            )
        ]
        warnings.append(
            "Personal files inside selected cleanup-category paths were left out of "
            "the large-file list; the category already accounts for that space."
        )

    notes = _build_notes(scan, discovery, personal_files)
    notes.extend(message for message in warnings if message not in notes)
    notes.extend(extra_notes)

    report = CleanupReport(
        title="AppSweep storage report",
        scan=scan,
        cleanup_items=items,
        personal_files=personal_files,
        notes=notes,
        scan_started=scan_started,
        generated_at=now_iso(),
        app_version=app_version,
    )
    return Analysis(report=report, warnings=warnings, overlaps=overlaps)


def _build_notes(
    scan: ScanResult,
    discovery: CleanupDiscovery,
    personal_files: list[FileSnapshot],
) -> list[str]:
    notes: list[str] = []
    if scan.cancelled:
        notes.append(
            "This scan was cancelled. Coverage is incomplete and all cleanup actions "
            "are disabled."
        )
    if scan.permission_error_count:
        notes.append(
            f"{scan.permission_error_count} location(s) could not be fully read. "
            "The report shows samples and does not pretend the scan was complete."
        )
    if scan.skipped_paths:
        notes.append(
            f"{len(scan.skipped_paths)} mount point(s) or paths were deliberately "
            "skipped. See skipped paths for the exact reasons."
        )
    if scan.sandboxed:
        notes.append(
            "AppSweep is running inside a Flatpak sandbox. Host package tools and "
            "host-only system paths are not reachable; only accessible local folders "
            "were analyzed."
        )

    filesystems = {fs.fstype.lower() for fs in scan.filesystems}
    if "btrfs" in filesystems:
        notes.append(
            "Btrfs notes: reported sizes are logical file sizes. Compression, reflinks, "
            "and shared extents mean physical space can be lower than the sum shown."
        )
    elif any(fs in filesystems for fs in ("xfs", "ext4")):
        notes.append(
            "Reported sizes are logical file sizes. Sparse files and filesystem "
            "features such as reflinks can make physical disk use lower."
        )
    notes.append(
        "Cleanup category sizes and personal-file sizes are separate and are not added "
        "to directory totals. Moving a personal file to Trash frees no disk space until "
        "Trash is emptied."
    )
    notes.append(
        "A selected personal file is moved to Trash by default. Permanent deletion is "
        "a separate choice and requires an extra confirmation."
    )
    if discovery.notes:
        # Preserve the most useful module notes without duplicating warnings.
        for message in discovery.notes:
            if message not in notes:
                notes.append(message)
    if personal_files:
        notes.append(
            f"The largest-files table lists {len(personal_files)} eligible personal "
            "file(s) at or above the configured minimum and excludes caches, Trash, "
            "hidden configuration, and system data."
        )
    else:
        notes.append(
            "No personal files matched the large-file threshold, or the selected scan "
            "folders contained none."
        )
    return notes


def _find_cleanup_overlaps(
    items: list[CleanupItem],
) -> tuple[list[str], set[str]]:
    warnings: list[str] = []
    overlapping_keys: set[str] = set()
    for index, first in enumerate(items):
        for second in items[index + 1 :]:
            if first.key == second.key:
                continue
            if any(
                _paths_overlap(path_a, path_b)
                for path_a in first.paths
                for path_b in second.paths
            ):
                warnings.append(
                    f"Cleanup overlap: '{first.title}' [{first.key}] overlaps "
                    f"'{second.title}' [{second.key}]."
                )
                overlapping_keys.update((first.key, second.key))
    return warnings, overlapping_keys


def _find_personal_category_overlaps(
    items: Iterable[CleanupItem], files: Iterable[FileSnapshot]
) -> list[str]:
    paths = [(item, path) for item in items for path in item.paths]
    warnings: list[str] = []
    for file in files:
        for item, path in paths:
            if _paths_overlap(file.path, path):
                warnings.append(
                    f"Personal file {file.path} overlaps cleanup item '{item.title}'. "
                    "It was removed from the personal file list."
                )
    return warnings


def _paths_overlap(first: str, second: str) -> bool:
    return is_within(first, second) or is_within(second, first)


def group_totals(items: Iterable[CleanupItem]) -> dict[RiskGroup, int]:
    totals = {group: 0 for group in RiskGroup}
    for item in items:
        if item.eligible:
            totals[item.group] += item.size_bytes
    return totals


def all_report_only(
    discovery: CleanupDiscovery,
    analysis: Analysis,
) -> list[ReportOnlyFinding]:
    findings = list(discovery.report_only)
    for item in analysis.report.cleanup_items:
        if not item.eligible:
            findings.append(
                ReportOnlyFinding(
                    title=item.title,
                    size_bytes=item.size_bytes,
                    detail=item.report_only_reason or item.summary,
                    reason=item.report_only_reason
                    or "This item is not eligible for automatic cleanup.",
                    group=item.group,
                )
            )
    findings.sort(key=lambda finding: (-finding.size_bytes, finding.title))
    return findings
