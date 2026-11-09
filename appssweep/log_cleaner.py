"""Journal, log, crash-dump, and temporary-file analysis.

Only systemd's official journal vacuum is offered as an automatic cleanup.
Rotated logs, active logs, crash dumps, and temporary files are report-only so
AppSweep never guesses that an in-use file is safe to remove.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .filesystem import measure_path
from .models import ActionKind, CleanupItem, ReportOnlyFinding, RiskGroup
from .runner import CommandRunner
from .util import format_command, human_bytes, parse_size

JOURNAL_KEEP_SIZE = 256 * 1024 * 1024
MIN_JOURNAL_RECLAIM = 8 * 1024 * 1024
JOURNAL_DIRS = ("/var/log/journal", "/run/log/journal")
ROTATED_SUFFIXES = (".gz", ".xz", ".bz2", ".zst", ".old")
ROTATED_PATTERN = re.compile(r"(?:\.\d+|-\d{8}|\.old)$")


@dataclass
class LogDiscovery:
    items: list[CleanupItem] = field(default_factory=list)
    report_only: list[ReportOnlyFinding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    journal_bytes: int = 0


def discover_logs(
    runner: CommandRunner | None = None,
    *,
    journal_keep_size: int = JOURNAL_KEEP_SIZE,
    include_temporary: bool = True,
) -> LogDiscovery:
    runner = runner or CommandRunner()
    discovery = LogDiscovery()
    _discover_journal(runner, discovery, journal_keep_size)
    _analyze_rotated_logs(discovery)
    _analyze_crash_dumps(discovery)
    if include_temporary:
        _analyze_temporary_files(discovery)
    return discovery


def _discover_journal(
    runner: CommandRunner,
    discovery: LogDiscovery,
    keep_size: int,
) -> None:
    journalctl = runner.find("journalctl")
    usage = 0
    source = ""
    if journalctl:
        result = runner.run([journalctl, "--disk-usage"], timeout=15)
        if result.ok:
            usage = parse_journal_disk_usage(result.output)
            source = "journalctl --disk-usage"
            discovery.notes.append(
                f"journalctl reports {human_bytes(usage)} in system journals."
            )
    if not usage:
        usage = _measure_journal_directories()
        if usage:
            source = "journal directory sizes"
            discovery.notes.append(
                f"Journal directory size: {human_bytes(usage)} "
                "(journalctl usage was not available)."
            )
    discovery.journal_bytes = usage

    if not journalctl:
        if usage:
            discovery.report_only.append(
                ReportOnlyFinding(
                    title="System journal",
                    size_bytes=usage,
                    detail="Journal storage measured from /var/log/journal and /run/log/journal.",
                    reason=(
                        "journalctl was not found, so AppSweep will not manually "
                        "delete journal files."
                    ),
                    group=RiskGroup.SAFE,
                )
            )
        return

    estimated = max(0, usage - keep_size)
    if estimated < MIN_JOURNAL_RECLAIM:
        discovery.notes.append(
            "Journal usage is already at or near the 256 MB cleanup target, so no "
            "journal vacuum is offered."
        )
        return
    target = f"{max(64, keep_size // (1024 * 1024))}M"
    args = (journalctl, "--vacuum-size=" + target)
    discovery.items.append(
        CleanupItem(
            key="system-journal",
            title="Old system journal data",
            group=RiskGroup.SAFE,
            size_bytes=estimated,
            summary=(
                f"Keep the newest {human_bytes(keep_size)} of journal and vacuum older "
                "entries."
            ),
            description=(
                f"Systemd journals currently take {human_bytes(usage)}. AppSweep asks "
                f"journalctl to keep the newest {human_bytes(keep_size)} and remove the rest."
            ),
            consequences=(
                "Older log messages will no longer be available for troubleshooting. "
                "Active journals remain safe: journalctl performs the vacuum."
            ),
            method=(
                "Runs the supported journalctl --vacuum-size operation. AppSweep never "
                "deletes files inside /var/log/journal itself."
            ),
            action=ActionKind.JOURNAL_VACUUM,
            command_args=(args,),
            needs_admin=True,
            estimated=True,
            details=(
                f"Current usage: {human_bytes(usage)}",
                f"Keep newest: {human_bytes(keep_size)}",
                f"Data source: {source or 'journal size measurement'}",
            ),
            preview=format_command(args),
            metadata={"usage": usage, "keep": keep_size},
        )
    )


def parse_journal_disk_usage(text: str) -> int:
    # journalctl commonly prints ``1.0G`` while other tools print ``1.0GiB``;
    # accept both forms so real journal output is measured correctly.
    match = re.search(
        r"([\d.]+)\s*([KMGTPE]?i?B?)\b", text, re.IGNORECASE
    )
    if not match:
        return 0
    try:
        return parse_size(f"{match.group(1)}{match.group(2)}")
    except ValueError:
        return 0


def _measure_journal_directories() -> int:
    total = 0
    for raw in JOURNAL_DIRS:
        if not os.path.isdir(raw):
            continue
        measurement = measure_path(raw)
        total += measurement.logical_size
    return total


def _analyze_rotated_logs(discovery: LogDiscovery) -> None:
    root = "/var/log"
    if not os.path.isdir(root) or os.path.islink(root):
        return
    total = 0
    count = 0
    names: list[str] = []
    try:
        entries = list(os.scandir(root))
    except OSError as exc:
        discovery.notes.append(f"Could not inspect /var/log: {exc.strerror or exc}")
        return
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                continue
            name = entry.name
            lower = name.lower()
            if not (
                lower.endswith(ROTATED_SUFFIXES)
                or ROTATED_PATTERN.search(lower)
            ):
                continue
            lstat = entry.stat(follow_symlinks=False)
            if lstat.st_uid != 0 and lstat.st_uid != os.getuid():
                continue
            total += lstat.st_size
            count += 1
            if len(names) < 12:
                names.append(name)
        except OSError:
            continue
    if total:
        discovery.report_only.append(
            ReportOnlyFinding(
                title="Rotated log files",
                size_bytes=total,
                detail=(
                    f"{count} rotated log file(s), including "
                    + ", ".join(names[:8])
                    + "."
                ),
                reason=(
                    "Log rotation is managed by logrotate. AppSweep does not delete "
                    "log files by hand, to avoid removing active or needed diagnostics."
                ),
                group=RiskGroup.SAFE,
            )
        )


def _analyze_crash_dumps(discovery: LogDiscovery) -> None:
    roots = (
        "/var/lib/systemd/coredump",
        str(Path.home() / ".cache" / "systemd-coredump"),
    )
    total = 0
    count = 0
    for raw in roots:
        if not os.path.isdir(raw) or os.path.islink(raw):
            continue
        try:
            entries = list(os.scandir(raw))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    continue
                total += entry.stat(follow_symlinks=False).st_size
                count += 1
            except OSError:
                continue
    if total:
        discovery.report_only.append(
            ReportOnlyFinding(
                title="System crash dumps",
                size_bytes=total,
                detail=f"{count} crash-dump file(s).",
                reason=(
                    "Crash dumps can be useful for diagnosing failures. They are shown "
                    "for analysis and are not removed automatically."
                ),
                group=RiskGroup.SAFE,
            )
        )


def _analyze_temporary_files(discovery: LogDiscovery) -> None:
    total = 0
    details: list[str] = []
    for raw in ("/tmp", "/var/tmp"):
        if not os.path.isdir(raw) or os.path.islink(raw):
            continue
        measurement = measure_path(
            raw,
            excluded_paths=("/tmp/systemd-private-", "/var/tmp/systemd-private-"),
        )
        if measurement.logical_size <= 0:
            continue
        total += measurement.logical_size
        details.append(f"{raw}: {human_bytes(measurement.logical_size)}")
        if measurement.permission_errors:
            details.append(
                f"{raw}: {len(measurement.permission_errors)} location(s) unreadable"
            )
    if total:
        discovery.report_only.append(
            ReportOnlyFinding(
                title="Temporary files",
                size_bytes=total,
                detail="; ".join(details),
                reason=(
                    "Temporary files may belong to running applications. AppSweep "
                    "reports them but does not offer automatic deletion."
                ),
                group=RiskGroup.SAFE,
            )
        )
