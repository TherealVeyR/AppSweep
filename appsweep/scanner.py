"""Fast, read-only filesystem traversal for AppSweep.

Design choices:
- os.scandir + entry.stat(follow_symlinks=False), never shell utilities per file
- one traversal per root
- filesystem-boundary and virtual/network-mount pruning
- no content reads, no hashing
- hardlink-aware accounting
- bounded top-N candidate maps rather than keeping every file in memory
- explicit permission-error and skipped-mount reporting
"""

from __future__ import annotations

import errno
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
    mount_for_path,
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
_SCANDIR_FD_SUPPORT = os.scandir in os.supports_fd
_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


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
        self._seen_inodes: set[tuple[int, int]] = set()
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
                if not (existing_dev == root_dev and is_within(root, existing)):
                    continue
                root_mount = mount_for_path(root, self.mounts)
                existing_mount = mount_for_path(existing, self.mounts)
                if (
                    root_mount is not None
                    and existing_mount is not None
                    and root_mount.mount_point != existing_mount.mount_point
                ):
                    # The parent traversal stops at the nested mount boundary,
                    # so this explicitly selected root must still be scanned.
                    continue
                skip = True
                break
            if not skip:
                roots.append(root)
        return tuple(roots)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def _cancelled(self) -> bool:
        """Consult the cancellation source, including convenience callbacks."""

        return bool(self.cancel_token())

    def scan(self) -> ScanResult:
        started = time.monotonic()
        self._scan_started = started
        self._seen_inodes.clear()
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
            if self._cancelled():
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
        elif self._cancelled():
            self.result.cancelled = True
            self.result.coverage_notes.append(
                "Scan was cancelled. Results are incomplete and cleanup is disabled."
            )

        self._finalize_top_dirs()
        self.result.elapsed_seconds = time.monotonic() - started
        if not self.result.cancelled and self.result.permission_error_count:
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
    @staticmethod
    def _open_scanned_directory(path: str) -> tuple[int, os.stat_result]:
        if (
            not _SCANDIR_FD_SUPPORT
            or not hasattr(os, "O_DIRECTORY")
            or not hasattr(os, "O_NOFOLLOW")
        ):
            raise OSError(
                errno.ENOTSUP,
                "safe no-follow directory traversal is unavailable",
            )
        fd = os.open(path, _DIRECTORY_FLAGS)
        try:
            opened = os.fstat(fd)
            if not stat.S_ISDIR(opened.st_mode):
                raise OSError(errno.ENOTDIR, "not a directory")
            return fd, opened
        except Exception:
            os.close(fd)
            raise

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
        personal_best: dict[tuple[int, int], FileSnapshot] = {}
        issue_count = 0

        root_norm = os.path.normpath(root)
        stack: list[tuple[str, str | None, int, int]] = [
            (root_norm, None, root_lstat.st_dev, root_lstat.st_ino)
        ]
        last_progress = 0.0
        entries_since_cancel_check = 0
        while stack:
            if self._cancelled():
                self.result.cancelled = True
                break
            current, inherited_app, expected_dev, expected_ino = stack.pop()
            app_label = self.config.application_roots.get(current, inherited_app)
            normalized_current = os.path.normpath(current)
            directory_sizes.setdefault(normalized_current, 0)
            directory_allocated.setdefault(normalized_current, 0)
            directory_files.setdefault(normalized_current, 0)

            if current != root_norm and self._should_skip_directory(
                current, root_dev, excludes
            ):
                continue

            try:
                directory_fd, opened = self._open_scanned_directory(current)
            except PermissionError as exc:
                issue_count += 1
                self._record_permission_issue(current, exc)
                continue
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EPERM):
                    issue_count += 1
                    self._record_permission_issue(current, exc)
                else:
                    self._record_skipped(
                        current,
                        "directory changed or was replaced during scan",
                        "",
                    )
                continue

            try:
                if (
                    opened.st_dev != expected_dev
                    or opened.st_ino != expected_ino
                    or not stat.S_ISDIR(opened.st_mode)
                ):
                    self._record_skipped(
                        current,
                        "directory changed or was replaced during scan",
                        "",
                    )
                    continue
                if opened.st_dev != root_dev:
                    self._record_skipped(
                        current,
                        "different filesystem (external or separate mount)",
                        "",
                    )
                    continue

                try:
                    iterator = os.scandir(directory_fd)
                except PermissionError as exc:
                    issue_count += 1
                    self._record_permission_issue(current, exc)
                    continue
                except OSError as exc:
                    if exc.errno in (errno.EACCES, errno.EPERM):
                        issue_count += 1
                        self._record_permission_issue(current, exc)
                    else:
                        self._record_skipped(
                            current,
                            "directory changed or was replaced during scan",
                            "",
                        )
                    continue

                self.result.total_dirs += 1

                with iterator:
                    for entry in iterator:
                        entries_since_cancel_check += 1
                        if entries_since_cancel_check >= 512:
                            entries_since_cancel_check = 0
                            if self._cancelled():
                                self.result.cancelled = True
                                stack.clear()
                                break

                        entry_path = os.path.normpath(
                            os.path.join(current, entry.name)
                        )
                        try:
                            lstat = os.stat(
                                entry.name,
                                dir_fd=directory_fd,
                                follow_symlinks=False,
                            )
                        except OSError as exc:
                            issue_count += 1
                            self._record_permission_issue(entry_path, exc)
                            continue

                        mode = lstat.st_mode
                        if stat.S_ISLNK(mode):
                            self.result.total_symlinks += 1
                            continue
                        if stat.S_ISDIR(mode):
                            child = entry_path
                            if is_within(child, current) or child == current:
                                # Keep a lexical parent map; it is cheap
                                # compared with storing complete file paths.
                                directory_parents[child] = normalized_current
                            if self._cancelled():
                                self.result.cancelled = True
                                stack.clear()
                                break
                            if self._should_skip_directory(
                                child, root_dev, excludes, lstat
                            ):
                                continue
                            child_app = self.config.application_roots.get(
                                child, app_label
                            )
                            stack.append(
                                (
                                    child,
                                    child_app,
                                    lstat.st_dev,
                                    lstat.st_ino,
                                )
                            )
                            continue
                        if not stat.S_ISREG(mode):
                            continue

                        self.result.total_files += 1
                        identity = (lstat.st_dev, lstat.st_ino)
                        count_storage = True
                        if lstat.st_nlink > 1:
                            if identity in self._seen_inodes:
                                # This pathname is another alias of an inode
                                # whose storage was already counted. It must
                                # still be evaluated for personal-file
                                # eligibility below.
                                count_storage = False
                            else:
                                self._seen_inodes.add(identity)

                        if count_storage:
                            self.result.unique_files += 1
                            self.result.total_bytes += lstat.st_size
                            allocated = _allocated_size(lstat)
                            self.result.total_allocated += allocated
                            directory_sizes[normalized_current] += lstat.st_size
                            directory_allocated[normalized_current] += allocated
                            directory_files[normalized_current] += 1

                            file_type = classify_path(entry_path)
                            self.result.file_type_sizes[file_type.kind.value] = (
                                self.result.file_type_sizes.get(
                                    file_type.kind.value, 0
                                )
                                + lstat.st_size
                            )
                            self.result.file_type_counts[file_type.kind.value] = (
                                self.result.file_type_counts.get(
                                    file_type.kind.value, 0
                                )
                                + 1
                            )
                            extension_key = file_type.extension or "(none)"
                            self.result.extension_sizes[extension_key] = (
                                self.result.extension_sizes.get(extension_key, 0)
                                + lstat.st_size
                            )
                            area = self._storage_area(entry_path)
                            self.result.storage_areas[area] = (
                                self.result.storage_areas.get(area, 0)
                                + lstat.st_size
                            )
                            if app_label:
                                self.result.application_usage[app_label] = (
                                    self.result.application_usage.get(app_label, 0)
                                    + lstat.st_size
                                )
                        else:
                            # A hard-link alias still needs a type for a possible
                            # personal snapshot, but its storage must not be
                            # counted again.
                            file_type = classify_path(entry_path)

                        if self._is_personal_candidate(
                            entry_path, lstat, app_label=app_label
                        ):
                            snapshot = self._snapshot_from_stat(
                                entry_path, lstat, file_type
                            )
                            self._remember_personal_candidate(
                                personal_best, snapshot
                            )

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
            finally:
                os.close(directory_fd)

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

        snapshots = list(personal_best.values())
        # Multiple roots are completed one by one; merge into the bounded list.
        # Hard-link aliases of the same inode are represented by one
        # deterministic pathname: the largest snapshot, then the smallest path.
        merged = self.result.top_files + snapshots
        deduped: dict[tuple[int, int], FileSnapshot] = {}
        for snapshot in merged:
            key = (snapshot.st_dev, snapshot.st_ino)
            existing = deduped.get(key)
            if existing is None or _personal_rank(snapshot) < _personal_rank(
                existing
            ):
                deduped[key] = snapshot
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
        if self._cancelled():
            self.result.cancelled = True

    def _scan_loose_file(self, path: str, lstat: os.stat_result) -> None:
        self.result.total_files += 1
        identity = (lstat.st_dev, lstat.st_ino)
        count_storage = True
        if lstat.st_nlink > 1:
            if identity in self._seen_inodes:
                count_storage = False
            else:
                self._seen_inodes.add(identity)

        file_type = classify_path(path)
        if count_storage:
            self.result.unique_files += 1
            self.result.total_bytes += lstat.st_size
            self.result.total_allocated += _allocated_size(lstat)
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
            self.result.storage_areas["Other"] = (
                self.result.storage_areas.get("Other", 0) + lstat.st_size
            )

        if self._is_personal_candidate(path, lstat, app_label=None):
            self._remember_loose_personal_file(
                self._snapshot_from_stat(path, lstat, file_type)
            )

    def _remember_personal_candidate(
        self,
        best: dict[tuple[int, int], FileSnapshot],
        snapshot: FileSnapshot,
    ) -> None:
        """Remember the best alias of an inode within the bounded Top N.

        A dict keeps one representative per inode and is trimmed to ``top_n``
        distinct inodes. This avoids stale heap entries for superseded aliases:
        such an entry could otherwise consume a slot and push a still-eligible
        inode out of the results.
        """

        key = (snapshot.st_dev, snapshot.st_ino)
        existing = best.get(key)
        if existing is not None and _personal_rank(existing) <= _personal_rank(
            snapshot
        ):
            return
        best[key] = snapshot
        if len(best) > self.config.top_n:
            worst_key = max(best, key=lambda item: _personal_rank(best[item]))
            del best[worst_key]

    def _remember_loose_personal_file(self, snapshot: FileSnapshot) -> None:
        key = (snapshot.st_dev, snapshot.st_ino)
        for index, existing in enumerate(self.result.top_files):
            if (existing.st_dev, existing.st_ino) != key:
                continue
            if _personal_rank(existing) <= _personal_rank(snapshot):
                return
            self.result.top_files[index] = snapshot
            break
        else:
            self.result.top_files.append(snapshot)
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
        if mount:
            if (
                mount.fstype.lower() in VIRTUAL_FSTYPES
                or is_network_filesystem(mount.fstype)
            ):
                self._record_skipped(
                    normalized, "virtual or network mount", mount.fstype
                )
            else:
                # This includes same-device bind mounts. A selected root at or
                # below the mount is scanned separately, so stopping here
                # avoids double counting and crossing an explicit boundary.
                self._record_skipped(
                    normalized, "nested mount boundary", mount.fstype
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
            ctime_ns=getattr(lstat, "st_ctime_ns", 0),
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


def _personal_rank(snapshot: FileSnapshot) -> tuple[int, str]:
    """Rank one hard-link alias deterministically within Top 50 results.

    Larger files rank first. When two eligible pathnames refer to the same
    inode, the lexicographically smaller pathname is the representative, so the
    result does not depend on scan-root traversal order.
    """

    return (-snapshot.size, snapshot.path)


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
