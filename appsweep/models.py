"""Shared data models for AppSweep.

The models deliberately contain no terminal code and no destructive actions.
Scanning, analysis, presentation, and cleanup are separate layers.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class RiskGroup(str, Enum):
    """The three user-facing safety groups."""

    SAFE = "safe"
    CAREFUL = "careful"
    REBUILD = "rebuild"

    @property
    def label(self) -> str:
        return {
            RiskGroup.SAFE: "SAFE",
            RiskGroup.CAREFUL: "CAREFUL",
            RiskGroup.REBUILD: "REBUILD",
        }[self]

    @property
    def explanation(self) -> str:
        return {
            RiskGroup.SAFE: "Temporary by-products. Apps may rebuild them, but no installed app or personal work is removed.",
            RiskGroup.CAREFUL: "Your files or Trash. Nothing is removed without a separate, explicit confirmation.",
            RiskGroup.REBUILD: "Package data that can be downloaded or installed again. Requires package-manager action.",
        }[self]


class ActionKind(str, Enum):
    """Allowlisted cleanup operation types.

    AppSweep never accepts an arbitrary command or path as an action. Every
    operation in this enum has a dedicated, validated implementation.
    """

    CLEAR_CACHE_CONTENTS = "clear_cache_contents"
    DNF_CLEAN_CACHE = "dnf_clean_cache"
    DNF_AUTOREMOVE = "dnf_autoremove"
    DNF_OLD_KERNELS = "dnf_old_kernels"
    FLATPAK_UNUSED = "flatpak_unused"
    JOURNAL_VACUUM = "journal_vacuum"
    EMPTY_TRASH = "empty_trash"
    RPM_OSTREE_METADATA = "rpm_ostree_metadata"
    TRASH_PERSONAL = "trash_personal"
    PERMANENT_PERSONAL = "permanent_personal"
    REPORT_ONLY = "report_only"


class FileKind(str, Enum):
    DISK_IMAGE = "Disk image"
    VIDEO = "Video"
    AUDIO = "Audio"
    ARCHIVE = "Archive"
    PACKAGE = "Package"
    INSTALLER = "Installer"
    PICTURE = "Picture"
    DOCUMENT = "Document"
    OTHER = "Other"
    UNKNOWN = "Unknown"


@dataclass(frozen=True)
class FileType:
    """A conservative, extension-based file description."""

    extension: str
    label: str
    kind: FileKind
    description: str


@dataclass
class FileSnapshot:
    """A regular file recorded during a scan.

    ``st_dev``, ``st_ino``, ``mtime_ns``, ``ctime_ns``, and ``size`` form an
    identity snapshot. Cleanup re-checks them so a numbered selection can never
    point at a different file if the directory changed after the scan. The
    change time also catches an in-place content edit that preserves the size
    and restores the original modification time.
    """

    path: str
    size: int
    allocated: int
    mtime: float
    st_dev: int
    st_ino: int
    extension: str
    type_label: str
    kind: FileKind
    description: str
    owner_uid: int
    group: RiskGroup = RiskGroup.CAREFUL
    ctime_ns: int = 0

    @property
    def filename(self) -> str:
        return Path(self.path).name

    @property
    def parent(self) -> str:
        return str(Path(self.path).parent)

    @property
    def mtime_ns(self) -> int:
        return int(self.mtime * 1_000_000_000)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "filename": self.filename,
            "size": self.size,
            "allocated": self.allocated,
            "mtime": self.mtime,
            "extension": self.extension,
            "type": self.type_label,
            "kind": self.kind.value,
            "description": self.description,
            "owner_uid": self.owner_uid,
            "group": self.group.value,
        }


@dataclass(frozen=True)
class FileIdentity:
    """Identity used when re-validating a cleanup target."""

    path: str
    st_dev: int
    st_ino: int
    size: int
    mtime_ns: int
    ctime_ns: int = 0


@dataclass
class DirectoryUsage:
    path: str
    size: int
    allocated: int
    file_count: int
    depth: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size": self.size,
            "allocated": self.allocated,
            "file_count": self.file_count,
            "depth": self.depth,
        }


@dataclass
class FilesystemInfo:
    path: str
    source: str
    fstype: str
    total: int
    used: int
    available: int
    mount_options: str = ""

    @property
    def used_percent(self) -> float:
        if self.total <= 0:
            return 0.0
        return (self.used / self.total) * 100.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "source": self.source,
            "fstype": self.fstype,
            "total": self.total,
            "used": self.used,
            "available": self.available,
            "used_percent": round(self.used_percent, 2),
            "mount_options": self.mount_options,
        }


@dataclass
class MountInfo:
    mount_point: str
    source: str
    fstype: str
    options: str = ""
    major_minor: str = ""


@dataclass
class PermissionIssue:
    path: str
    reason: str


@dataclass
class SkippedPath:
    path: str
    reason: str
    fstype: str = ""


@dataclass
class ScanConfig:
    mode: str = "quick"
    roots: tuple[str, ...] = ()
    min_file_size: int = 1 * 1024 * 1024
    top_n: int = 50
    top_dirs: int = 20
    excluded_paths: tuple[str, ...] = ()
    personal_roots: tuple[str, ...] = ()
    personal_excluded_paths: tuple[str, ...] = ()
    application_roots: dict[str, str] = field(default_factory=dict)
    follow_symlinks: bool = False
    detect_mounts: bool = True
    include_system_files: bool = True
    progress_interval: float = 0.12

    def normalized(self) -> ScanConfig:
        roots = _unique_existing_paths(self.roots)
        personal = _unique_existing_paths(self.personal_roots) or tuple(roots)
        return ScanConfig(
            mode=self.mode,
            roots=roots,
            min_file_size=max(0, int(self.min_file_size)),
            top_n=max(1, int(self.top_n)),
            top_dirs=max(1, int(self.top_dirs)),
            excluded_paths=_normalize_abs_paths(self.excluded_paths),
            personal_roots=personal,
            personal_excluded_paths=_normalize_abs_paths(self.personal_excluded_paths),
            application_roots={
                _normalize_abs_path(k): v for k, v in self.application_roots.items()
            },
            follow_symlinks=False,
            detect_mounts=self.detect_mounts,
            include_system_files=self.include_system_files,
            progress_interval=self.progress_interval,
        )


@dataclass
class ScanResult:
    config: ScanConfig
    filesystems: list[FilesystemInfo] = field(default_factory=list)
    roots: tuple[str, ...] = ()
    total_files: int = 0
    unique_files: int = 0
    total_dirs: int = 0
    total_symlinks: int = 0
    total_bytes: int = 0
    total_allocated: int = 0
    top_files: list[FileSnapshot] = field(default_factory=list)
    top_dirs: list[DirectoryUsage] = field(default_factory=list)
    file_type_sizes: dict[str, int] = field(default_factory=dict)
    file_type_counts: dict[str, int] = field(default_factory=dict)
    extension_sizes: dict[str, int] = field(default_factory=dict)
    storage_areas: dict[str, int] = field(default_factory=dict)
    application_usage: dict[str, int] = field(default_factory=dict)
    permission_issues: list[PermissionIssue] = field(default_factory=list)
    permission_error_count: int = 0
    skipped_paths: list[SkippedPath] = field(default_factory=list)
    excluded_paths: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    cancelled: bool = False
    sandboxed: bool = False
    coverage_notes: list[str] = field(default_factory=list)
    mounted_filesystems: list[MountInfo] = field(default_factory=list)

    @property
    def filesystem(self) -> FilesystemInfo | None:
        return self.filesystems[0] if self.filesystems else None

    @property
    def personal_files_size(self) -> int:
        return sum(f.size for f in self.top_files)

    @property
    def largest_personal_file(self) -> FileSnapshot | None:
        return self.top_files[0] if self.top_files else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": {
                "mode": self.config.mode,
                "roots": list(self.roots),
                "min_file_size": self.config.min_file_size,
                "top_n": self.config.top_n,
                "excluded_paths": list(self.config.excluded_paths),
            },
            "filesystems": [fs.to_dict() for fs in self.filesystems],
            "coverage": {
                "files_seen": self.total_files,
                "unique_files": self.unique_files,
                "directories": self.total_dirs,
                "symlinks": self.total_symlinks,
                "logical_bytes": self.total_bytes,
                "allocated_bytes": self.total_allocated,
                "permission_errors": self.permission_error_count,
                "cancelled": self.cancelled,
                "elapsed_seconds": round(self.elapsed_seconds, 3),
                "notes": list(self.coverage_notes),
            },
            "top_personal_files": [f.to_dict() for f in self.top_files],
            "largest_directories": [d.to_dict() for d in self.top_dirs],
            "file_types": {
                label: {
                    "bytes": self.file_type_sizes.get(label, 0),
                    "count": self.file_type_counts.get(label, 0),
                }
                for label in self.file_type_sizes
            },
            "storage_areas": dict(
                sorted(self.storage_areas.items(), key=lambda kv: kv[1], reverse=True)
            ),
            "applications": dict(
                sorted(self.application_usage.items(), key=lambda kv: kv[1], reverse=True)
            ),
            "permission_issues": [
                {"path": p.path, "reason": p.reason} for p in self.permission_issues
            ],
            "skipped_paths": [
                {"path": p.path, "reason": p.reason, "fstype": p.fstype}
                for p in self.skipped_paths
            ],
        }


@dataclass
class CleanupItem:
    """A selectable cleanup opportunity.

    Values are discovered from the live system. The ``action`` field is an
    allowlisted enum, never a shell command. ``command_args`` is retained only
    for transparent previews and validated execution by the cleanup engine.
    """

    key: str
    title: str
    group: RiskGroup
    size_bytes: int
    summary: str
    description: str
    consequences: str
    method: str
    action: ActionKind
    paths: tuple[str, ...] = ()
    command_args: tuple[tuple[str, ...], ...] = ()
    needs_admin: bool = False
    estimated: bool = False
    eligible: bool = True
    report_only_reason: str = ""
    details: tuple[str, ...] = ()
    preview: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    number: int = 0

    @property
    def action_label(self) -> str:
        if not self.eligible:
            return "Report only"
        if self.action == ActionKind.TRASH_PERSONAL:
            return "Move to Trash"
        if self.action == ActionKind.PERMANENT_PERSONAL:
            return "Delete permanently"
        if self.action == ActionKind.CLEAR_CACHE_CONTENTS:
            return "Clear cache contents"
        if self.action == ActionKind.EMPTY_TRASH:
            return "Empty Trash"
        return "Use system tool"

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "key": self.key,
            "title": self.title,
            "group": self.group.value,
            "size_bytes": self.size_bytes,
            "summary": self.summary,
            "description": self.description,
            "consequences": self.consequences,
            "method": self.method,
            "action": self.action.value,
            "paths": list(self.paths),
            "command_preview": list(self.command_args),
            "needs_admin": self.needs_admin,
            "estimated": self.estimated,
            "eligible": self.eligible,
            "report_only_reason": self.report_only_reason,
            "details": list(self.details),
            "preview": self.preview,
        }


@dataclass
class ReportOnlyFinding:
    title: str
    size_bytes: int
    detail: str
    reason: str
    group: RiskGroup = RiskGroup.SAFE

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "size_bytes": self.size_bytes,
            "detail": self.detail,
            "reason": self.reason,
            "group": self.group.value,
        }


@dataclass
class CleanupPlan:
    cleanup_items: list[CleanupItem] = field(default_factory=list)
    personal_files: list[FileSnapshot] = field(default_factory=list)
    permanent_personal: bool = False

    @property
    def cleanup_total(self) -> int:
        seen: set[str] = set()
        total = 0
        for item in self.cleanup_items:
            if item.key not in seen:
                total += item.size_bytes
                seen.add(item.key)
        return total

    @property
    def personal_total(self) -> int:
        return sum(f.size for f in self.personal_files)

    @property
    def total(self) -> int:
        return self.cleanup_total + self.personal_total

    @property
    def is_empty(self) -> bool:
        return not self.cleanup_items and not self.personal_files

    def describe(self) -> str:
        parts = []
        if self.cleanup_items:
            parts.append(f"{len(self.cleanup_items)} cleanup item(s)")
        if self.personal_files:
            verb = (
                "requested for disabled permanent deletion"
                if self.permanent_personal
                else "moved to Trash"
            )
            parts.append(f"{len(self.personal_files)} personal file(s) {verb}")
        return ", ".join(parts) if parts else "nothing selected"


@dataclass(frozen=True)
class Authorization:
    """Proof that a person confirmed a particular plan in this process.

    The CLI creates this only after the confirmation screen. The engine will
    not run unless every selected object is covered by the authorization.
    """

    cleanup_keys: frozenset[str]
    personal_paths: frozenset[str]
    permanent_personal: bool


@dataclass
class OperationResult:
    title: str
    success: bool
    reclaimed_bytes: int = 0
    message: str = ""
    command: str = ""
    skipped: bool = False
    needs_admin: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "success": self.success,
            "reclaimed_bytes": self.reclaimed_bytes,
            "message": self.message,
            "command": self.command,
            "skipped": self.skipped,
            "needs_admin": self.needs_admin,
        }


@dataclass
class ExecutionReport:
    results: list[OperationResult] = field(default_factory=list)
    available_before: int = 0
    available_after: int = 0
    filesystem_path: str = ""

    @property
    def reclaimed_bytes(self) -> int:
        return sum(r.reclaimed_bytes for r in self.results if r.success)

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.success and not r.skipped)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.skipped)

    @property
    def filesystem_freed(self) -> int:
        return max(0, self.available_after - self.available_before)

    def to_dict(self) -> dict[str, Any]:
        return {
            "filesystem_path": self.filesystem_path,
            "available_before": self.available_before,
            "available_after": self.available_after,
            "reported_reclaimed_bytes": self.reclaimed_bytes,
            "filesystem_freed_bytes": self.filesystem_freed,
            "results": [r.to_dict() for r in self.results],
        }


@dataclass
class CleanupReport:
    """A complete report assembled for terminal, JSON, and CSV output."""

    title: str
    scan: ScanResult
    cleanup_items: list[CleanupItem]
    personal_files: list[FileSnapshot]
    notes: list[str] = field(default_factory=list)
    scan_started: str = ""
    generated_at: str = ""
    app_version: str = ""

    @property
    def safe_items(self) -> list[CleanupItem]:
        return [i for i in self.cleanup_items if i.group == RiskGroup.SAFE]

    @property
    def rebuild_items(self) -> list[CleanupItem]:
        return [i for i in self.cleanup_items if i.group == RiskGroup.REBUILD]

    @property
    def careful_items(self) -> list[CleanupItem]:
        return [i for i in self.cleanup_items if i.group == RiskGroup.CAREFUL]

    @property
    def eligible_cleanup_items(self) -> list[CleanupItem]:
        return [i for i in self.cleanup_items if i.eligible]

    @property
    def confirmed_cleanup_total(self) -> int:
        return sum(i.size_bytes for i in self.eligible_cleanup_items if not i.estimated)

    @property
    def estimated_cleanup_total(self) -> int:
        return sum(i.size_bytes for i in self.eligible_cleanup_items if i.estimated)


ProgressCallback = Callable[[str, int, int, int, float], None]
CancelCheck = Callable[[], bool]


def _normalize_abs_paths(paths: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        if not raw:
            continue
        try:
            path = _normalize_abs_path(raw)
        except (OSError, ValueError, RuntimeError):
            continue
        if path not in seen:
            seen.add(path)
            result.append(path)
    return tuple(result)


def _normalize_abs_path(path: str) -> str:
    return os.path.normpath(os.path.abspath(os.path.expanduser(path)))


def _unique_existing_paths(paths: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in _normalize_abs_paths(paths):
        try:
            if not Path(raw).exists():
                continue
        except (OSError, ValueError):
            continue
        if raw not in seen:
            seen.add(raw)
            result.append(raw)
    return tuple(result)
