"""Trash inspection and explicit emptying.

Trash contents are personal data. AppSweep reports them as ``careful`` and
uses the desktop's supported ``gio trash --empty`` operation. If gio is not
available, AppSweep does not substitute a manual recursive deletion.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .filesystem import measure_path
from .models import ActionKind, CleanupItem, ReportOnlyFinding, RiskGroup
from .runner import CommandRunner
from .util import format_command, human_bytes


@dataclass
class TrashDiscovery:
    items: list[CleanupItem] = field(default_factory=list)
    report_only: list[ReportOnlyFinding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    trash_root: str = ""
    total_size: int = 0
    file_count: int = 0


def discover_trash(runner: CommandRunner | None = None) -> TrashDiscovery:
    runner = runner or CommandRunner()
    discovery = TrashDiscovery()
    data_home = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    trash_root = os.path.join(os.path.expanduser(data_home), "Trash")
    if not os.path.isdir(trash_root):
        discovery.notes.append("No user Trash folder was found.")
        return discovery
    if os.path.islink(trash_root):
        discovery.report_only.append(
            ReportOnlyFinding(
                title="User Trash",
                size_bytes=0,
                detail=trash_root,
                reason="The Trash path is a symbolic link, so it is not inspected or emptied.",
                group=RiskGroup.CAREFUL,
            )
        )
        return discovery

    measurement = measure_path(trash_root)
    if measurement.incomplete:
        discovery.notes.append(
            "Some Trash contents could not be read; the reported size is a lower bound."
        )
    if measurement.logical_size <= 0 and measurement.files <= 0:
        discovery.notes.append("Trash is empty.")
        return discovery

    files_dir = os.path.join(trash_root, "files")
    file_count = 0
    example_names: list[str] = []
    try:
        if os.path.isdir(files_dir) and not os.path.islink(files_dir):
            with os.scandir(files_dir) as entries:
                for entry in entries:
                    file_count += 1
                    if len(example_names) < 8:
                        example_names.append(entry.name)
    except OSError:
        file_count = 0
        example_names = []

    discovery.trash_root = trash_root
    discovery.total_size = measurement.logical_size
    discovery.file_count = file_count
    gio = runner.find("gio")
    if not gio:
        discovery.report_only.append(
            ReportOnlyFinding(
                title="User Trash",
                size_bytes=measurement.logical_size,
                detail=f"{file_count} item(s) in {trash_root}",
                reason=(
                    "gio is not installed. AppSweep will not manually delete Trash "
                    "files as a fallback."
                ),
                group=RiskGroup.CAREFUL,
            )
        )
        return discovery

    args = (gio, "trash", "--empty")
    details = [
        f"Location: {trash_root}",
        f"Top-level items: {file_count}",
        "This action permanently deletes the contents.",
    ]
    if example_names:
        more = " (more not shown)" if file_count > len(example_names) else ""
        details.insert(
            2,
            "Examples: " + ", ".join(example_names) + more,
        )
    discovery.items.append(
        CleanupItem(
            key="user-trash",
            title="User Trash",
            group=RiskGroup.CAREFUL,
            size_bytes=measurement.logical_size,
            summary=f"{file_count} item(s) in your desktop Trash.",
            description=(
                f"Trash currently holds {human_bytes(measurement.logical_size)}. "
                "This is data you previously moved to Trash, not cache or package data."
            ),
            consequences=(
                "Emptying Trash is immediate and irreversible. It also removes Trash "
                "items from other applications that use the same desktop Trash. If any "
                "file might still be needed, restore it before confirming."
            ),
            method=(
                "Runs gio trash --empty, the desktop's supported Trash operation. "
                "No fallback direct deletion is used if gio fails."
            ),
            action=ActionKind.EMPTY_TRASH,
            command_args=(args,),
            needs_admin=False,
            estimated=False,
            details=tuple(details),
            preview=format_command(args),
            metadata={
                "trash_root": trash_root,
                "file_count": file_count,
                "example_names": tuple(example_names),
            },
        )
    )
    discovery.notes.append(
        "Emptying Trash permanently removes its contents; moving files to Trash earlier "
        "did not free disk space yet."
    )
    return discovery
