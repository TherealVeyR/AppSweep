"""Machine-readable report export.

The terminal UI is intentionally handled elsewhere. This module turns the
exact report the person saw into JSON and CSV data, without rescanning or
reordering anything. The numbered personal-file list and cleanup numbers are
preserved so an exported report can be audited or discussed later.
"""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .analyzer import Analysis, group_totals
from .categories import CleanupDiscovery
from .models import CleanupReport, FileSnapshot
from .util import human_bytes, timestamp_from_epoch

CSV_COLUMNS = (
    "record_type",
    "number",
    "group",
    "title",
    "filename",
    "path",
    "size_bytes",
    "size_human",
    "file_type",
    "modified",
    "estimated",
    "eligible",
    "description",
    "consequences",
    "method",
)

# Spreadsheet programs may treat a cell that starts with one of these
# characters as a formula. File names and descriptions are attacker-controlled
# data, so CSV exports neutralize them rather than letting a report execute
# something when it is opened in a spreadsheet.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


class ExportError(RuntimeError):
    """A report could not be written to the requested location."""


def csv_safe_cell(value: Any) -> Any:
    """Return a spreadsheet-safe version of one CSV cell.

    Numeric values are left untouched. Text that could be interpreted as a
    formula is prefixed with an apostrophe, which spreadsheet programs display
    as literal text. This also covers values hidden behind leading whitespace.
    """

    if not isinstance(value, str):
        return value
    if value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    stripped = value.lstrip(" \t\r\n")
    if stripped and stripped[0] in "=+-@" and stripped != value:
        return "'" + value
    return value


def report_to_dict(
    report: CleanupReport,
    analysis: Analysis | None = None,
    discovery: CleanupDiscovery | None = None,
) -> dict[str, Any]:
    """Build the complete export payload shown in the terminal."""

    totals = group_totals(report.cleanup_items)
    payload: dict[str, Any] = {
        "app": "AppSweep",
        "app_version": report.app_version,
        "title": report.title,
        "generated_at": report.generated_at,
        "scan_started": report.scan_started,
        "summary": {
            "confirmed_cleanup_bytes": report.confirmed_cleanup_total,
            "estimated_cleanup_bytes": report.estimated_cleanup_total,
            "personal_files_bytes": sum(file.size for file in report.personal_files),
            "personal_files_count": len(report.personal_files),
            "eligible_cleanup_items": len(report.eligible_cleanup_items),
            "group_totals": {
                group.value: size for group, size in totals.items()
            },
            "report_only_findings": (
                len(discovery.report_only) if discovery is not None else 0
            ),
        },
        "scan": report.scan.to_dict(),
        "cleanup_items": [
            {**item.to_dict(), "action_label": item.action_label}
            for item in report.cleanup_items
        ],
        "personal_files": [
            {**file.to_dict(), "modified": timestamp_from_epoch(file.mtime)}
            for file in report.personal_files
        ],
        "notes": list(report.notes),
    }
    if analysis is not None:
        payload["warnings"] = list(analysis.warnings)
        payload["overlaps"] = list(analysis.overlaps)
    if discovery is not None:
        payload["categories_run"] = list(discovery.categories_run)
        payload["capabilities"] = discovery.capabilities.to_dict()
        payload["report_only_findings"] = [
            finding.to_dict() for finding in discovery.report_only
        ]
        payload["package_manager"] = (
            {
                "kind": discovery.package_info.kind,
                "label": discovery.package_info.label,
                "version": discovery.package_info.version,
                "available": discovery.package_info.available,
            }
            if discovery.package_info is not None
            else None
        )
        payload["flatpak"] = (
            {
                "available": discovery.flatpak_info.available,
                "version": discovery.flatpak_info.version,
            }
            if discovery.flatpak_info is not None
            else None
        )
    return payload


def render_json(
    report: CleanupReport,
    analysis: Analysis | None = None,
    discovery: CleanupDiscovery | None = None,
) -> str:
    return json.dumps(
        report_to_dict(report, analysis, discovery),
        indent=2,
        ensure_ascii=False,
    ) + "\n"


def render_csv(
    report: CleanupReport,
    analysis: Analysis | None = None,
    discovery: CleanupDiscovery | None = None,
) -> str:
    """Render one CSV with a record_type column.

    Personal files and cleanup items have different columns in the terminal,
    but a single CSV keeps the exact scan snapshot together and avoids two
    files with subtly different numbering. The ``record_type`` column makes the
    two kinds of rows easy to filter in a spreadsheet.
    """

    del analysis  # kept for a consistent export API
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS)
    writer.writeheader()

    def write_row(row: dict[str, Any]) -> None:
        writer.writerow({key: csv_safe_cell(value) for key, value in row.items()})

    for index, file in enumerate(report.personal_files, start=1):
        write_row(
            {
                "record_type": "personal_file",
                "number": index,
                "group": "careful",
                "title": file.filename,
                "filename": file.filename,
                "path": file.path,
                "size_bytes": file.size,
                "size_human": human_bytes(file.size),
                "file_type": file.type_label,
                "modified": timestamp_from_epoch(file.mtime),
                "estimated": "no",
                "eligible": "yes",
                "description": file.description,
                "consequences": (
                    "Moved to Trash only in this release; permanent deletion is "
                    "disabled because same-UID quarantine isolation cannot be "
                    "guaranteed. Moving to Trash frees no space until Trash is "
                    "emptied."
                ),
                "method": "Desktop Trash operation (gio); no permanent-deletion fallback.",
            }
        )

    for item in report.cleanup_items:
        write_row(
            {
                "record_type": "cleanup_item",
                "number": item.number,
                "group": item.group.value,
                "title": item.title,
                "filename": "",
                "path": " | ".join(item.paths),
                "size_bytes": item.size_bytes,
                "size_human": human_bytes(item.size_bytes),
                "file_type": "",
                "modified": "",
                "estimated": "yes" if item.estimated else "no",
                "eligible": "yes" if item.eligible else "no",
                "description": item.description,
                "consequences": item.consequences,
                "method": item.method,
            }
        )

    for finding in (discovery.report_only if discovery is not None else ()):
        write_row(
            {
                "record_type": "report_only",
                "number": "",
                "group": finding.group.value,
                "title": finding.title,
                "filename": "",
                "path": "",
                "size_bytes": finding.size_bytes,
                "size_human": human_bytes(finding.size_bytes),
                "file_type": "",
                "modified": "",
                "estimated": "yes",
                "eligible": "no",
                "description": finding.detail,
                "consequences": "Not changed by AppSweep.",
                "method": finding.reason,
            }
        )
    return buffer.getvalue()


def write_export(path: str, text: str) -> None:
    """Atomically write a report, with a clear error instead of a traceback."""

    if path == "-":
        raise ExportError("write_export() needs a file path, not standard output")
    destination = Path(path).expanduser().absolute()
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ExportError(
            f"Could not create the report folder {destination.parent}: "
            f"{exc.strerror or exc}"
        ) from exc

    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            dir=str(destination.parent),
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, destination)
    except OSError as exc:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
        raise ExportError(
            f"Could not write {destination}: {exc.strerror or exc}"
        ) from exc


def file_rows_for_csv(files: Iterable[FileSnapshot]) -> list[dict[str, Any]]:
    """Small helper used by tests and third-party callers."""

    return [
        {
            "number": index,
            "path": file.path,
            "size": file.size,
            "type": file.type_label,
            "modified": timestamp_from_epoch(file.mtime),
        }
        for index, file in enumerate(files, start=1)
    ]
