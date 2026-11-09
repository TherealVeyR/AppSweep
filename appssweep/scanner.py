"""Fast, read-only filesystem traversal for AppSweep.

Design choices:
- os.scandir + entry.stat(follow_symlinks=False), never shell utilities per file
- one traversal per root
- filesystem-boundary and virtual/network-mount pruning
- no content reads, no hashing
- hardlink-aware accounting
- bounded top-N heaps rather than keeping every file in memory
- explicit permission-error and skipped-mount reporting
"""

from __future__ import annotations

import heapq
import os
import stat
import threading
import time
from collections.abc import Iterable
from pathlib import Path

from .filesystem import (
    BASE_EXCLUDES,
    VIRTUAL_FSTYPES,
    filesystem_infos,
    is_flatpak_sandbox,
    is_network_filesystem,
    read_mountinfo,
)
from .filetypes import classify_path
from .models import (
    CancelCheck,
    DirectoryUsage,
    FileSnapshot,
    MountInfo,
    PermissionIssue,
    ProgressCallback,
    RiskGroup,
    ScanConfig,
    ScanResult,
    SkippedPath,
)
from .util import human_bytes, is_within, path_depth

DEFAULT_MAX_ISSUE_DETAILS = 60
HARD_LINK_CHUNK = 4096


class ScanCancelled(Exception):
    """Raised when a scanner is used in an API context that wants an exception."""


class CancellationToken:
    """Thread-safe cancellation token usable by UIs and tests."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def reset(self) -> None:
        self._event.clear()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def __call__(self) -> bool:
        return self._event.is_set()


class StorageScanner:
    def __init__(
        self,
        config: ScanConfig,
        *,
        progress: ProgressCallback | None = None,
        cancel_token: CancellationToken | None = None,
        mounts: Iterable[MountInfo] | None = None,
    ) -> None:
        self.config = config.normalized()
        self.progress = progress
        self.cancel_token = cancel_token or CancellationToken()
        self.mounts = list(mounts) if mounts is not None else read_mountinfo()
        self.mount_map = {m.mount_point: m for m in self.mounts}
        self._scan_started = time.monotonic()
        self.result = ScanResult(
            config=self.config,
            roots=self.config.roots,
            sandboxed=is_flatpak_sandbox(),
        )

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------
    def _combined_excludes(self) -> tuple[str, ...]:
        excludes = list(BASE_EXCLUDES)
        excludes.extend(self.config.excluded_paths)
        # Never descend into our own virtual or network mounts, even if they
        # are not in the base list. If the person explicitly chose a scan root
        # inside such a mount, that mount is not excluded wholesale.
        scan_roots = tuple(self.config.roots)
        for mount in self.mounts:
            if (
                (
                    mount.fstype.lower() in VIRTUAL_FSTYPES
                    or is_network_filesystem(mount.fstype)
                )
                and mount.mount_point != "/"
                and not any(is_within(root, mount.mount_point) for root in scan_roots)
            ):
                excludes.append(mount.mount_point)
        deduped: list[str] = []
        seen: set[str] = set()
        for path in excludes:
            normalized = os.path.normpath(os.path.abspath(path))
            if normalized not in seen:
                seen.add(normalized)
                deduped.append(normalized)
        return tuple(deduped)

    def _roots_without_same_device_overlap(self) -> tuple[str, ...]:
        roots: list[str] = []
        for root in sorted(self.config.roots, key=lambda p: path_depth(p)):
            try:
                root_dev = os.stat(root).st_dev
            except OSError:
                continue
            skip = False
            for existing in roots:
                try:
                    existing_dev = os.stat(existing).st_dev
                except OSError:
                    continue
                if existing_dev == root_dev and is_within(root, existing):
                    skip = True
                    break
            if not skip:
                roots.append(root)
        return tuple(roots)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def scan(self) -> ScanResult:
        started = time.monotonic()
        self._scan_started = started
        self.result.filesystems = filesystem_infos(
            self._roots_without_same_device_overlap() or self.config.roots,
            self.mounts,
        )
        excludes = self._combined_excludes()
        self.result.excluded_paths = list(excludes)
        self.result.mounted_filesystems = list(self.mounts)

        roots = self._roots_without_same_device_overlap()
        if not roots:
            self.result.coverage_notes.append("No readable scan roots were available.")
            self.result.elapsed_seconds = time.monotonic() - started
            return self.result

        for root in roots:
            if self.cancel_token.cancelled:
                self.result.cancelled = True
                break
            try:
                self._scan_root(root, excludes)
            except KeyboardInterrupt:
                self.cancel_token.cancel()
                self.result.cancelled = True
                break

        if self.result.cancelled:
            self.result.coverage_notes.append(
                "Scan was cancelled. Results are incomplete and cleanup is disabled."
            )

        self._finalize_top_dirs()
        self.result.elapsed_seconds = time.monotonic() - started
        if not self.cancel_token.cancelled and self.result.permission_error_count:
            self.result.coverage_notes.append(
                f"{self.result.permission_error_count} location(s) could not be fully read; "
                "the report lists samples instead of pretending coverage is complete."
            )
        if self.result.top_files:
            self.result.coverage_notes.append(
                f"Top personal file list includes up to {self.config.top_n} files "
                "at or above the minimum size, ranked by logical size."
            )
        return self.result

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------
    def _scan_root(self, root: str, excludes: tuple[str, ...]) -> None:
        try:
            root_lstat = os.lstat(root)
        except OSError as exc:
            self._record_permission_issue(root, exc)
            return

        if stat.S_ISREG(root_lstat.st_mode):
            self._scan_loose_file(root, root_lstat)
            return
        if stat.S_ISLNK(root_lstat.st_mode):
            self.result.coverage_notes.append(
                f"Scan root {root} is a symbolic link. AppSweep does not follow "
                "symbolic links, so this root was not scanned."
            )
            return
        if not stat.S_ISDIR(root_lstat.st_mode):
            return

        try:
            root_dev = root_lstat.st_dev
        except AttributeError:
            root_dev = 0

        # Top directory propagation maps. Every visited directory is present
        # in directory_sizes so a size can bubble up once, after the scan.
        directory_sizes: dict[str, int] = {}
        directory_allocated: dict[str, int] = {}
        directory_files: dict[str, int] = {}
        directory_parents: dict[str, str | None] = {
            os.path.normpath(root): None
        }
        seen_inodes: set[tuple[int, int]] = set()
        personal_heap: list[tuple[int, str, FileSnapshot]] = []
        issue_count = 0

        stack: list[tuple[str, str | None]] = [(os.path.normpath(root), None)]
        last_progress = 0.0
        entries_since_cancel_check = 0
        while stack:
            if self.cancel_token.cancelled:
                self.result.cancelled = True
                break
            current, inherited_app = stack.pop()
            app_label = self.config.application_roots.get(current, inherited_app)
            normalized_current = os.path.normpath(current)
            directory_sizes.setdefault(normalized_current, 0)
            directory_allocated.setdefault(normalized_current, 0)
            directory_files.setdefault(normalized_current, 0)

            if current != os.path.normpath(root) and self._should_skip_directory(
                current, root_dev, excludes
            ):
                continue

            try:
                with os.scandir(current) as iterator:
                    entries = list(iterator)
            except PermissionError as exc:
                issue_count += 1
                self._record_permission_issue(current, exc)
                continue
            except OSError as exc:
                issue_count += 1
                self._record_permission_issue(current, exc)
                continue

            self.result.total_dirs += 1

            for entry in entries:
                entries_since_cancel_check += 1
                if entries_since_cancel_check >= 512:
                    entries_since_cancel_check = 0
                    if self.cancel_token.cancelled:
                        self.result.cancelled = True
                        stack.clear()
                        break

                try:
                    lstat = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    issue_count += 1
                    self._record_permission_issue(entry.path, exc)
                    continue

                mode = lstat.st_mode
                if stat.S_ISLNK(mode):
                    self.result.total_symlinks += 1
                    continue
                if stat.S_ISDIR(mode):
                    child = os.path.normpath(entry.path)
                    if is_within(child, current) or child == current:
                        # Keep a lexical parent map; it is cheap compared with
                        # storing complete file paths.
                        directory_parents[child] = normalized_current
                    if self.cancel_token.cancelled:
                        self.result.cancelled = True
                        stack.clear()
                        break
                    if self._should_skip_directory(child, root_dev, excludes, lstat):
                        continue
                    child_app = self.config.application_roots.get(child, app_label)
                    stack.append((child, child_app))
                    continue
                if not stat.S_ISREG(mode):
                    continue

                self.result.total_files += 1
                identity = (lstat.st_dev, lstat.st_ino)
                if lstat.st_nlink > 1:
                    if identity in seen_inodes:
                        continue
                    seen_inodes.add(identity)
                self.result.unique_files += 1
                self.result.total_bytes += lstat.st_size
                allocated = _allocated_size(lstat)
                self.result.total_allocated += allocated
                directory_sizes[normalized_current] += lstat.st_size
                directory_allocated[normalized_current] += allocated
                directory_files[normalized_current] += 1

                file_type = classify_path(entry.path)
                self.result.file_type_sizes[file_type.kind.value] = (
                    self.result.file_type_sizes.get(file_type.kind.value, 0)
                    + lstat.st_size
                )
                self.result.file_type_counts[file_type.kind.value] = (
                    self.result.file_type_counts.get(file_type.kind.value, 0) + 1
                )
                extension_key = file_type.extension or "(none)"
                self.result.extension_sizes[extension_key] = (
                    self.result.extension_sizes.get(extension_key, 0) + lstat.st_size
                )
                area = self._storage_area(entry.path)
                self.result.storage_areas[area] = (
                    self.result.storage_areas.get(area, 0) + lstat.st_size
                )
                if app_label:
                    self.result.application_usage[app_label] = (
                        self.result.application_usage.get(app_label, 0) + lstat.st_size
                    )

                if self._is_personal_candidate(
                    entry.path, lstat, app_label=app_label
                ):
                    snapshot = self._snapshot_from_stat(
                        entry.path, lstat, file_type
                    )
                    heapq.heappush(
                        personal_heap, (snapshot.size, snapshot.path, snapshot)
                    )
                    if len(personal_heap) > self.config.top_n:
                        heapq.heappop(personal_heap)

            if self.progress is not None:
                now = time.monotonic()
                if now - last_progress >= self.config.progress_interval:
                    last_progress = now
                    self.progress(
                        current,
                        self.result.unique_files,
                        self.result.total_dirs,
                        self.result.total_bytes,
                        now - self._scan_started,
                    )

        # Bubble exclusive sizes up exactly once per directory.
        all_dirs = set(directory_sizes) | set(directory_allocated) | set(directory_files)
        for directory in sorted(all_dirs, key=path_depth, reverse=True):
            parent = directory_parents.get(directory)
            if not parent:
                continue
            if parent not in directory_sizes and parent not in directory_allocated:
                continue
            directory_sizes[parent] = directory_sizes.get(parent, 0) + directory_sizes.get(
                directory, 0
            )
            directory_allocated[parent] = directory_allocated.get(
                parent, 0
            ) + directory_allocated.get(directory, 0)
            directory_files[parent] = directory_files.get(parent, 0) + directory_files.get(
                directory, 0
            )

        for directory, size in directory_sizes.items():
            if size <= 0:
                continue
            self.result.top_dirs.append(
                DirectoryUsage(
                    path=directory,
                    size=size,
                    allocated=directory_allocated.get(directory, 0),
                    file_count=directory_files.get(directory, 0),
                    depth=path_depth(directory),
                )
            )
        # Keep the in-progress list bounded when scanning many roots.
        self.result.top_dirs = sorted(
            self.result.top_dirs, key=lambda d: (-d.size, d.path)
        )[: self.config.top_dirs]

        snapshots = [heap_item[2] for heap_item in personal_heap]
        snapshots.sort(key=lambda item: (-item.size, item.path))
        # Multiple roots are completed one by one; merge into the bounded list.
        merged = self.result.top_files + snapshots
        deduped: dict[tuple[int, int], FileSnapshot] = {}
        for snapshot in merged:
            deduped.setdefault((snapshot.st_dev, snapshot.st_ino), snapshot)
        self.result.top_files = sorted(
            deduped.values(), key=lambda item: (-item.size, item.path)
        )[: self.config.top_n]

        if self.progress is not None:
            self.progress(
                root,
                self.result.unique_files,
                self.result.total_dirs,
                self.result.total_bytes,
                time.monotonic() - self._scan_started,
            )
        if self.cancel_token.cancelled:
            self.result.cancelled = True

    def _scan_loose_file(self, path: str, lstat: os.stat_result) -> None:
        self.result.total_files += 1
        self.result.unique_files += 1
        self.result.total_bytes += lstat.st_size
        self.result.total_allocated += _allocated_size(lstat)
        file_type = classify_path(path)
        self.result.file_type_sizes[file_type.kind.value] = (
            self.result.file_type_sizes.get(file_type.kind.value, 0) + lstat.st_size
        )
        self.result.file_type_counts[file_type.kind.value] = (
            self.result.file_type_counts.get(file_type.kind.value, 0) + 1
        )
        extension_key = file_type.extension or "(none)"
        self.result.extension_sizes[extension_key] = (
            self.result.extension_sizes.get(extension_key, 0) + lstat.st_size
        )
        self.result.storage_areas["Other"] = (
            self.result.storage_areas.get("Other", 0) + lstat.st_size
        )
        if self._is_personal_candidate(path, lstat, app_label=None):
            self.result.top_files.append(self._snapshot_from_stat(path, lstat, file_type))
            self.result.top_files.sort(key=lambda item: (-item.size, item.path))
            del self.result.top_files[self.config.top_n :]

    def _finalize_top_dirs(self) -> None:
        self.result.top_dirs = sorted(
            self.result.top_dirs, key=lambda item: (-item.size, item.path)
        )[: self.config.top_dirs]
        self.result.total_dirs = max(self.result.total_dirs, len(self.result.top_dirs))

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------
    def _should_skip_directory(
        self,
        path: str,
        root_dev: int,
        excludes: Iterable[str],
        lstat: os.stat_result | None = None,
    ) -> bool:
        normalized = os.path.normpath(os.path.abspath(path))
        for excluded in excludes:
            if is_within(normalized, excluded):
                self._record_skipped(
                    normalized, "excluded by scan rules", ""
                )
                return True
        if lstat is not None and lstat.st_dev != root_dev:
            self._record_skipped(
                normalized, "different filesystem (external or separate mount)", ""
            )
            return True
        mount = self.mount_map.get(normalized)
        if mount and (
            mount.fstype.lower() in VIRTUAL_FSTYPES
            or is_network_filesystem(mount.fstype)
        ):
            self._record_skipped(
                normalized, "virtual or network mount", mount.fstype
            )
            return True
        return False

    def _record_skipped(self, path: str, reason: str, fstype: str) -> None:
        if len(self.result.skipped_paths) >= 40:
            return
        if any(skip.path == path for skip in self.result.skipped_paths):
            return
        self.result.skipped_paths.append(
            SkippedPath(path=path, reason=reason, fstype=fstype)
        )

    def _record_permission_issue(self, path: str, exc: BaseException) -> None:
        self.result.permission_error_count += 1
        if len(self.result.permission_issues) < DEFAULT_MAX_ISSUE_DETAILS:
            reason = getattr(exc, "strerror", None) or str(exc) or exc.__class__.__name__
            self.result.permission_issues.append(
                PermissionIssue(path=path, reason=str(reason))
            )

    def _is_personal_candidate(
        self,
        path: str,
        lstat: os.stat_result,
        *,
        app_label: str | None,
    ) -> bool:
        if lstat.st_size < self.config.min_file_size:
            return False
        if lstat.st_uid != os.getuid():
            return False
        if not self.config.personal_roots:
            return False

        matched_root: str | None = None
        for root in self.config.personal_roots:
            if is_within(path, root) and (
                matched_root is None or len(root) > len(matched_root)
            ):
                matched_root = root
        if not matched_root:
            return False

        normalized = os.path.normpath(os.path.abspath(path))
        for excluded in self.config.personal_excluded_paths:
            if is_within(normalized, excluded):
                return False

        relative = os.path.relpath(normalized, matched_root)
        for part in Path(relative).parts:
            if part.startswith("."):
                return False

        # App-cache directories are cleanup categories, not personal files.
        if ".cache" in Path(normalized).parts:
            return False
        # Trash is reported in its own category and may contain deleted work.
        return "/.local/share/Trash/" not in normalized

    def _snapshot_from_stat(self, path: str, lstat: os.stat_result, file_type) -> FileSnapshot:
        return FileSnapshot(
            path=os.path.normpath(os.path.abspath(path)),
            size=lstat.st_size,
            allocated=_allocated_size(lstat),
            mtime=lstat.st_mtime,
            st_dev=lstat.st_dev,
            st_ino=lstat.st_ino,
            extension=file_type.extension,
            type_label=file_type.label,
            kind=file_type.kind,
            description=file_type.description,
            owner_uid=lstat.st_uid,
            group=RiskGroup.CAREFUL,
        )

    def _storage_area(self, path: str) -> str:
        normalized = os.path.normpath(os.path.abspath(path))
        for root in self.config.personal_roots:
            if not is_within(normalized, root):
                continue
            relative = os.path.relpath(normalized, root)
            first = Path(relative).parts[0] if relative != "." else ""
            if first in {"Downloads", "Desktop", "Documents", "Videos", "Music", "Pictures"}:
                return first
            if first.startswith("."):
                return "Application data & caches"
            return "Home (other)"

        if is_within(normalized, "/usr"):
            return "System /usr"
        if is_within(normalized, "/var"):
            return "System /var"
        if is_within(normalized, "/etc"):
            return "System /etc"
        if is_within(normalized, "/opt"):
            return "System /opt"
        if is_within(normalized, "/tmp"):
            return "Temporary files"
        if is_within(normalized, "/boot"):
            return "System /boot"
        if is_within(normalized, str(Path.home())):
            return "Home (other)"
        return "Other"


def _allocated_size(stats: os.stat_result) -> int:
    blocks = getattr(stats, "st_blocks", None)
    if blocks is None:
        return int(stats.st_size)
    return max(0, int(blocks) * 512)


def scan(
    config: ScanConfig,
    *,
    progress: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> ScanResult:
    """Convenience API used by tests and future GUI frontends."""

    token = CancellationToken()
    if cancel_check is not None:
        # A foreign cancellation source is checked inside the scan loop.
        class ForeignToken(CancellationToken):
            def __call__(self) -> bool:  # type: ignore[override]
                if cancel_check():
                    token.cancel()
                return super().__call__()

        token = ForeignToken()  # type: ignore[assignment]
    return StorageScanner(config, progress=progress, cancel_token=token).scan()


def format_progress_line(
    current: str,
    files: int,
    directories: int,
    size: int,
    elapsed_seconds: float,
) -> str:
    elapsed = max(0.001, float(elapsed_seconds))
    rate = files / elapsed if elapsed else 0
    return (
        f"Scanning… {files:,} files · {directories:,} folders · "
        f"{human_bytes(size)} · {rate:,.0f} files/s · {current}"
    )
