"""Safety policy and validated filesystem operations.

This module is deliberately conservative. If a path cannot be confidently
identified as a valid scan result or an allowlisted cache location, cleanup is
refused and a report-only reason is returned.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .models import Authorization, FileIdentity, FileSnapshot, RiskGroup
from .util import is_within

# Capture support at import time so test doubles replacing ``os`` functions do
# not make the platform look incapable of descriptor-relative deletion.
_DIR_FD_SUPPORT = {
    name: getattr(os, name) in os.supports_dir_fd
    for name in ("open", "stat", "unlink", "rmdir")
}
_SCANDIR_FD_SUPPORT = os.scandir in os.supports_fd

# These paths are never the target of a recursive removal. Package tools may
# operate on their own internal data through allowlisted commands, but
# AppSweep's filesystem deleter must not touch them directly.
# Exact paths which no cleanup target may equal. /home and /tmp are protected
# as folders, but legitimate personal/cache targets inside them are validated
# separately below.
PROTECTED_SYSTEM_PATHS = (
    "/",
    "/bin",
    "/boot",
    "/boot/efi",
    "/dev",
    "/etc",
    "/home",
    "/lib",
    "/lib32",
    "/lib64",
    "/lost+found",
    "/media",
    "/mnt",
    "/opt",
    "/proc",
    "/root",
    "/run",
    "/sbin",
    "/srv",
    "/sys",
    "/tmp",
    "/usr",
    "/var",
    "/var/lib",
    "/var/log",
)

# No direct filesystem cleanup is ever allowed beneath these trees.
PROTECTED_SYSTEM_TREES = (
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/lib",
    "/lib32",
    "/lib64",
    "/proc",
    "/root",
    "/run",
    "/sbin",
    "/sys",
    "/usr",
    "/var",
)

PROTECTED_HOME_TREES = (
    ".ssh",
    ".gnupg",
    ".pki",
    ".config",
    ".local",
    ".mozilla",
    ".thunderbird",
    ".password-store",
    ".var",
    ".steam",
    ".wine",
    ".android",
    ".docker",
)

# A discovered cache root may live inside one of the protected home trees.
# Only Flatpak's application cache area is allowed to override the generic
# home-tree protection; sensitive trees such as .ssh and .gnupg never are.
CACHE_HOME_OVERRIDES = frozenset({".var"})

# Files smaller than this are not interesting as a large-file operation. The
# snapshot identity check remains authoritative.
MIN_PERSONAL_DELETE_SIZE = 0


class SafetyError(Exception):
    """A cleanup target did not pass validation."""


@dataclass
class Validation:
    ok: bool
    reason: str = ""
    path: str = ""
    identity: FileIdentity | None = None
    target_dev: int | None = None
    target_ino: int | None = None
    target_mode: int = 0
    target_size: int = 0
    parent_dev: int | None = None
    parent_ino: int | None = None


@dataclass
class RemovalResult:
    removed_files: int = 0
    removed_dirs: int = 0
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    bytes_removed: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


class SafetyPolicy:
    def __init__(
        self,
        *,
        uid: int | None = None,
        home: str | None = None,
        personal_roots: Iterable[str] = (),
        cache_roots: Iterable[str] = (),
        trash_roots: Iterable[str] = (),
        protected_paths: Iterable[str] = PROTECTED_SYSTEM_PATHS,
        protected_trees: Iterable[str] = PROTECTED_SYSTEM_TREES,
        protected_home_names: Iterable[str] = PROTECTED_HOME_TREES,
    ) -> None:
        self.uid = os.getuid() if uid is None else uid
        self.home = os.path.normpath(
            os.path.abspath(os.path.expanduser(home or str(Path.home())))
        )
        self.personal_roots = tuple(
            sorted(
                _sorted_unique_roots(personal_roots or (self.home,)),
                key=len,
                reverse=True,
            )
        )
        self.cache_roots = tuple(_sorted_unique_roots(cache_roots))
        self.trash_roots = tuple(_sorted_unique_roots(trash_roots))
        self.protected_paths = tuple(_sorted_unique_roots(protected_paths))
        self.protected_trees = tuple(_sorted_unique_roots(protected_trees))
        self.protected_home_names = tuple(protected_home_names)
        self._cache_roots = self.cache_roots

    # ------------------------------------------------------------------
    # General path checks
    # ------------------------------------------------------------------
    def is_system_protected(self, path: str) -> bool:
        normalized = _norm(path)
        if normalized == "/":
            return True
        for protected in self.protected_paths:
            if normalized == protected:
                return True
        return False

    def is_protected(
        self, path: str, *, allow_explicit_cache_root: bool = False
    ) -> tuple[bool, str]:
        normalized = _norm(path)
        if normalized == "/":
            return True, "the filesystem root"
        if normalized == self.home:
            return True, "your entire home folder"
        for protected in self.protected_paths:
            if normalized == protected:
                return True, f"protected system location {protected}"
        for root in self.personal_roots:
            if normalized == root:
                return True, f"the scan root itself ({root})"
        for protected in self.protected_trees:
            if is_within(normalized, protected):
                return True, f"inside protected system location {protected}"
        for name in self.protected_home_names:
            protected_home = os.path.join(self.home, name)
            if normalized == protected_home or is_within(normalized, protected_home):
                if (
                    allow_explicit_cache_root
                    and name in CACHE_HOME_OVERRIDES
                    and self._is_flatpak_app_cache(normalized)
                    and any(is_within(normalized, root) for root in self.cache_roots)
                ):
                    continue
                return True, f"protected personal data ({name})"
        return False, ""

    def _is_flatpak_app_cache(self, path: str) -> bool:
        """Return whether *path* is inside a Flatpak app's cache directory.

        The generic protected-home override is intentionally narrower than the
        whole ``.var`` tree: application data, configuration, and other Flatpak
        state must never become selectable cache cleanup.
        """

        try:
            relative = os.path.relpath(_norm(path), self.home)
        except ValueError:
            return False
        parts = Path(relative).parts
        return (
            len(parts) >= 4
            and parts[0] == ".var"
            and parts[1] == "app"
            and parts[3] == "cache"
        )

    def has_symlink_component(self, path: str, *, stop_at: str | None = None) -> bool:
        """Check path components without following the final file.

        A parent symlink can redirect a deletion to an unrelated location. The
        scanner never follows symlinks, and cleanup re-validates this.
        """

        normalized = _norm(path)
        stop = _norm(stop_at) if stop_at else "/"
        if not is_within(normalized, stop):
            return True
        current = Path(stop)
        if Path(stop).is_symlink():
            return True
        try:
            relative_parts = Path(os.path.relpath(normalized, stop)).parts
        except ValueError:
            return True
        for part in relative_parts[:-1]:
            current = current / part
            try:
                if current.is_symlink():
                    return True
            except OSError:
                return True
        return False

    # ------------------------------------------------------------------
    # Personal files
    # ------------------------------------------------------------------
    def validate_personal_snapshot(self, snapshot: FileSnapshot) -> Validation:
        path = _norm(snapshot.path)
        if path != snapshot.path:
            return Validation(False, "path is not normalized")
        if not self._is_within_personal_root(path):
            return Validation(False, "file is outside your allowed personal scan roots")
        if snapshot.group != RiskGroup.CAREFUL:
            return Validation(False, "only personal files use this operation")
        if snapshot.owner_uid != self.uid:
            return Validation(False, "file is not owned by your user account")
        protected, reason = self.is_protected(path)
        if protected:
            return Validation(False, reason)
        anchor = self._best_personal_anchor(path)
        if anchor is None:
            return Validation(False, "file is outside your allowed personal scan roots")
        if self.has_symlink_component(path, stop_at=anchor):
            return Validation(False, "a parent path component is a symbolic link")
        try:
            lstat = os.lstat(path)
        except FileNotFoundError:
            return Validation(False, "file no longer exists")
        except OSError as exc:
            return Validation(False, f"cannot inspect file: {exc.strerror or exc}")
        if stat.S_ISLNK(lstat.st_mode):
            return Validation(False, "file is now a symbolic link")
        if not stat.S_ISREG(lstat.st_mode):
            return Validation(False, "file is no longer a regular file")
        if lstat.st_uid != self.uid:
            return Validation(False, "file ownership changed since the scan")
        if lstat.st_dev != snapshot.st_dev or lstat.st_ino != snapshot.st_ino:
            return Validation(False, "file identity changed since the scan")
        if lstat.st_size != snapshot.size:
            return Validation(False, "file size changed since the scan")
        if int(lstat.st_mtime * 1_000_000_000) != snapshot.mtime_ns:
            return Validation(False, "file modification time changed since the scan")
        if ".cache" in Path(path).parts or "/.local/share/Trash/" in path:
            return Validation(False, "cache and Trash entries have their own cleanup actions")
        relative = os.path.relpath(path, anchor)
        if any(part.startswith(".") for part in Path(relative).parts):
            return Validation(False, "hidden personal files are not offered for deletion")
        parent = os.path.dirname(path)
        try:
            parent_stat = os.stat(parent, follow_symlinks=False)
        except OSError as exc:
            return Validation(
                False, f"cannot inspect parent directory: {exc.strerror or exc}"
            )
        if not stat.S_ISDIR(parent_stat.st_mode):
            return Validation(False, "parent path is no longer a directory")
        return Validation(
            True,
            path=path,
            identity=FileIdentity(
                path=path,
                st_dev=lstat.st_dev,
                st_ino=lstat.st_ino,
                size=lstat.st_size,
                mtime_ns=int(lstat.st_mtime * 1_000_000_000),
            ),
            target_dev=lstat.st_dev,
            target_ino=lstat.st_ino,
            target_mode=lstat.st_mode,
            target_size=lstat.st_size,
            parent_dev=parent_stat.st_dev,
            parent_ino=parent_stat.st_ino,
        )

    def _is_within_personal_root(self, path: str) -> bool:
        return any(is_within(path, root) for root in self.personal_roots)

    def _best_personal_anchor(self, path: str) -> str | None:
        candidates = [root for root in self.personal_roots if is_within(path, root)]
        return max(candidates, key=len) if candidates else None

    # ------------------------------------------------------------------
    # Cache directories
    # ------------------------------------------------------------------
    def validate_cache_target(self, path: str) -> Validation:
        normalized = _norm(path)
        if not self.cache_roots:
            return Validation(False, "no cache locations were authorized")
        top_level_cache = os.path.normpath(
            os.path.abspath(
                os.environ.get("XDG_CACHE_HOME", os.path.join(self.home, ".cache"))
            )
        )
        if normalized == top_level_cache:
            return Validation(
                False,
                "the top-level user cache folder is never cleared as one action",
            )
        if not any(is_within(normalized, root) for root in self.cache_roots):
            return Validation(False, "path is not inside an approved cache location")
        protected, reason = self.is_protected(
            normalized, allow_explicit_cache_root=True
        )
        if protected:
            return Validation(False, reason)
        if self.has_symlink_component(normalized, stop_at=self._best_anchor(normalized)):
            return Validation(False, "a path component is a symbolic link")
        try:
            lstat = os.lstat(normalized)
        except FileNotFoundError:
            return Validation(False, "cache target no longer exists")
        except OSError as exc:
            return Validation(False, f"cannot inspect cache target: {exc.strerror or exc}")
        if stat.S_ISLNK(lstat.st_mode):
            return Validation(False, "cache target is a symbolic link")
        if not (stat.S_ISDIR(lstat.st_mode) or stat.S_ISREG(lstat.st_mode)):
            return Validation(False, "cache target is not a file or directory")
        if stat.S_ISDIR(lstat.st_mode) and lstat.st_uid != self.uid and lstat.st_uid != 0:
            return Validation(False, "cache target is owned by another user")
        parent = os.path.dirname(normalized)
        try:
            parent_stat = os.stat(parent, follow_symlinks=False)
        except OSError as exc:
            return Validation(
                False, f"cannot inspect cache parent directory: {exc.strerror or exc}"
            )
        if not stat.S_ISDIR(parent_stat.st_mode):
            return Validation(False, "cache parent path is no longer a directory")
        return Validation(
            True,
            path=normalized,
            target_dev=lstat.st_dev,
            target_ino=lstat.st_ino,
            target_mode=lstat.st_mode,
            target_size=lstat.st_size,
            parent_dev=parent_stat.st_dev,
            parent_ino=parent_stat.st_ino,
        )

    def _best_anchor(self, path: str) -> str:
        candidates = [root for root in self.cache_roots if is_within(path, root)]
        return max(candidates, key=len) if candidates else self.home

    # ------------------------------------------------------------------
    # Trash detection (emptying uses gio, not direct deletion)
    # ------------------------------------------------------------------
    def trash_roots_exist(self) -> bool:
        return any(os.path.isdir(root) for root in self.trash_roots)

    # ------------------------------------------------------------------
    # Deletion primitives
    # ------------------------------------------------------------------
    def remove_personal_file(self, snapshot: FileSnapshot) -> RemovalResult:
        self._require_secure_delete_primitives()
        validation = self.validate_personal_snapshot(snapshot)
        if not validation.ok:
            raise SafetyError(validation.reason)
        anchor = self._best_personal_anchor(validation.path)
        if anchor is None:
            raise SafetyError("file is outside your allowed personal scan roots")
        parent_path = os.path.dirname(validation.path)
        name = os.path.basename(validation.path)
        if not name or name in {".", ".."} or os.sep in name:
            raise SafetyError("selected file name is not safe for deletion")
        if validation.parent_dev is None or validation.parent_ino is None:
            raise SafetyError("selected parent directory identity is unavailable")

        anchor_fd = -1
        parent_fd = -1
        try:
            anchor_fd = self._open_verified_directory(
                anchor, "approved personal root"
            )
            relative_parent = os.path.relpath(parent_path, anchor)
            parent_fd = self._open_directory_chain(
                anchor_fd, relative_parent, parent_path, "selected parent directory"
            )
            self._verify_directory_path(
                parent_path,
                parent_fd,
                validation.parent_dev,
                validation.parent_ino,
                "selected parent directory",
            )
            final_stat = self._verify_selected_file(
                parent_fd, name, snapshot, validation
            )
            os.unlink(name, dir_fd=parent_fd)
        except SafetyError:
            raise
        except OSError as exc:
            raise SafetyError(f"could not delete {validation.path}: {exc}") from exc
        finally:
            if parent_fd >= 0:
                os.close(parent_fd)
            if anchor_fd >= 0:
                os.close(anchor_fd)

        result = RemovalResult()
        result.removed_files = 1
        result.bytes_removed = final_stat.st_size
        return result

    def clear_cache_target(self, path: str) -> RemovalResult:
        """Remove a cache target or its contents without following symlinks.

        The target directory itself is preserved. Filesystems inside a cache
        directory are never crossed. Every destructive operation is relative to
        an opened directory descriptor so a pathname swap cannot redirect it.
        """

        self._require_secure_delete_primitives()
        validation = self.validate_cache_target(path)
        if not validation.ok:
            raise SafetyError(validation.reason)
        if (
            validation.target_dev is None
            or validation.target_ino is None
            or validation.parent_dev is None
            or validation.parent_ino is None
        ):
            raise SafetyError("cache target identity is unavailable")

        normalized = validation.path
        anchor = self._best_anchor(normalized)
        parent_path = os.path.dirname(normalized)
        name = os.path.basename(normalized)
        if not name or name in {".", ".."} or os.sep in name:
            raise SafetyError("cache target name is not safe for deletion")

        result = RemovalResult()
        anchor_fd = -1
        target_fd = -1
        parent_fd = -1
        try:
            anchor_fd = self._open_verified_directory(anchor, "approved cache root")
            if stat.S_ISREG(validation.target_mode):
                relative_parent = os.path.relpath(parent_path, anchor)
                parent_fd = self._open_directory_chain(
                    anchor_fd,
                    relative_parent,
                    parent_path,
                    "cache parent directory",
                )
                self._verify_directory_path(
                    parent_path,
                    parent_fd,
                    validation.parent_dev,
                    validation.parent_ino,
                    "cache parent directory",
                )
                final_stat = self._verify_cache_file(
                    parent_fd, name, validation, normalized
                )
                os.unlink(name, dir_fd=parent_fd)
                result.removed_files = 1
                result.bytes_removed = final_stat.st_size
                return result

            if not stat.S_ISDIR(validation.target_mode):
                raise SafetyError("cache target is not a regular file or folder")

            relative_target = os.path.relpath(normalized, anchor)
            target_fd = self._open_directory_chain(
                anchor_fd, relative_target, normalized, "cache target"
            )
            self._verify_directory_path(
                normalized,
                target_fd,
                validation.target_dev,
                validation.target_ino,
                "cache target",
            )
            target_stat = os.fstat(target_fd)
            if target_stat.st_dev != validation.target_dev:
                raise SafetyError("cache target crossed a filesystem boundary")
            root_mount_id = self._mount_id_for_fd(target_fd)
            if root_mount_id is None:
                raise SafetyError(
                    "cannot verify the cache mount boundary on this system; "
                    "refusing cleanup"
                )
            self._remove_contents_fd(
                target_fd,
                target_stat.st_dev,
                root_mount_id,
                result,
                display_path=normalized,
                top=True,
            )
            return result
        except SafetyError:
            raise
        except OSError as exc:
            raise SafetyError(f"could not clear cache target {normalized}: {exc}") from exc
        finally:
            if parent_fd >= 0:
                os.close(parent_fd)
            if target_fd >= 0:
                os.close(target_fd)
            if anchor_fd >= 0:
                os.close(anchor_fd)

    # ------------------------------------------------------------------
    # Descriptor-relative filesystem primitives
    # ------------------------------------------------------------------
    def _require_secure_delete_primitives(self) -> None:
        missing: list[str] = []
        if os.name != "posix":
            missing.append("POSIX descriptors")
        if not hasattr(os, "O_DIRECTORY"):
            missing.append("O_DIRECTORY")
        if not hasattr(os, "O_NOFOLLOW"):
            missing.append("O_NOFOLLOW")
        for name, supported in _DIR_FD_SUPPORT.items():
            if not supported:
                missing.append(f"{name}(dir_fd=...)")
        if not _SCANDIR_FD_SUPPORT:
            missing.append("scandir(fd=...)")
        if missing:
            raise SafetyError(
                "safe descriptor-relative deletion is unavailable on this "
                f"platform (missing: {', '.join(missing)}); refusing cleanup"
            )

    @staticmethod
    def _directory_flags() -> int:
        return (
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
        )

    @staticmethod
    def _mount_id_for_fd(fd: int) -> int | None:
        """Return the Linux mount ID for an opened descriptor, if available."""

        try:
            with open(
                f"/proc/self/fdinfo/{fd}",
                "r",
                encoding="ascii",
                errors="replace",
            ) as handle:
                for line in handle:
                    if line.startswith("mnt_id:"):
                        return int(line.split(":", 1)[1].strip())
        except (OSError, ValueError):
            return None
        return None

    def _open_verified_directory(self, path: str, description: str) -> int:
        try:
            fd = os.open(path, self._directory_flags())
        except OSError as exc:
            raise SafetyError(
                f"cannot safely open {description} {path}: {exc.strerror or exc}"
            ) from exc
        try:
            opened = os.fstat(fd)
            if not stat.S_ISDIR(opened.st_mode):
                raise SafetyError(f"{description} {path} is not a directory")
            return fd
        except Exception:
            os.close(fd)
            raise

    def _open_directory_chain(
        self,
        anchor_fd: int,
        relative: str,
        display_path: str,
        description: str,
    ) -> int:
        relative = os.path.normpath(relative)
        if os.path.isabs(relative):
            raise SafetyError(f"{description} {display_path} is not below its root")
        parts: tuple[str, ...] = ()
        if relative not in {"", "."}:
            parts = Path(relative).parts
        if any(part in {"", ".", ".."} or os.sep in part for part in parts):
            raise SafetyError(f"{description} {display_path} has an unsafe path")
        fd = os.dup(anchor_fd)
        try:
            for part in parts:
                try:
                    child_fd = os.open(
                        part, self._directory_flags(), dir_fd=fd
                    )
                except OSError as exc:
                    raise SafetyError(
                        f"cannot safely open {description} {display_path}: "
                        f"{exc.strerror or exc}"
                    ) from exc
                os.close(fd)
                fd = child_fd
            return fd
        except Exception:
            os.close(fd)
            raise

    def _verify_directory_path(
        self,
        path: str,
        fd: int,
        expected_dev: int,
        expected_ino: int,
        description: str,
    ) -> None:
        try:
            path_stat = os.stat(path, follow_symlinks=False)
            fd_stat = os.fstat(fd)
        except OSError as exc:
            raise SafetyError(
                f"{description} changed or disappeared before deletion: "
                f"{exc.strerror or exc}"
            ) from exc
        if (
            not stat.S_ISDIR(path_stat.st_mode)
            or not stat.S_ISDIR(fd_stat.st_mode)
            or path_stat.st_dev != fd_stat.st_dev
            or path_stat.st_ino != fd_stat.st_ino
            or fd_stat.st_dev != expected_dev
            or fd_stat.st_ino != expected_ino
        ):
            raise SafetyError(
                f"{description} changed immediately before deletion; refused"
            )

    def _verify_selected_file(
        self,
        parent_fd: int,
        name: str,
        snapshot: FileSnapshot,
        validation: Validation,
    ) -> os.stat_result:
        try:
            final_stat = os.stat(
                name, dir_fd=parent_fd, follow_symlinks=False
            )
        except OSError as exc:
            raise SafetyError(
                f"selected file changed or disappeared before deletion: "
                f"{exc.strerror or exc}"
            ) from exc
        if stat.S_ISLNK(final_stat.st_mode):
            raise SafetyError("selected file became a symbolic link; refused")
        if not stat.S_ISREG(final_stat.st_mode):
            raise SafetyError("selected file is no longer a regular file; refused")
        if final_stat.st_uid != self.uid:
            raise SafetyError("selected file ownership changed; refused")
        if (
            final_stat.st_dev != snapshot.st_dev
            or final_stat.st_ino != snapshot.st_ino
            or (
                validation.target_dev is not None
                and final_stat.st_dev != validation.target_dev
            )
            or (
                validation.target_ino is not None
                and final_stat.st_ino != validation.target_ino
            )
        ):
            raise SafetyError("selected file identity changed; refused")
        if final_stat.st_size != snapshot.size:
            raise SafetyError("selected file size changed; refused")
        if int(final_stat.st_mtime * 1_000_000_000) != snapshot.mtime_ns:
            raise SafetyError("selected file modification time changed; refused")
        return final_stat

    def _verify_cache_file(
        self,
        parent_fd: int,
        name: str,
        validation: Validation,
        display_path: str,
    ) -> os.stat_result:
        try:
            final_stat = os.stat(
                name, dir_fd=parent_fd, follow_symlinks=False
            )
        except OSError as exc:
            raise SafetyError(
                f"cache file {display_path} changed or disappeared: "
                f"{exc.strerror or exc}"
            ) from exc
        if not stat.S_ISREG(final_stat.st_mode):
            raise SafetyError(f"cache file {display_path} is no longer a regular file")
        if (
            final_stat.st_dev != validation.target_dev
            or final_stat.st_ino != validation.target_ino
        ):
            raise SafetyError(f"cache file {display_path} identity changed; refused")
        return final_stat

    def _unlink_verified_entry(
        self,
        directory_fd: int,
        name: str,
        expected: os.stat_result,
        display_path: str,
        result: RemovalResult,
    ) -> os.stat_result | None:
        try:
            current = os.stat(
                name, dir_fd=directory_fd, follow_symlinks=False
            )
        except OSError as exc:
            result.errors.append(f"{display_path}: {exc.strerror or exc}")
            return None
        if (
            current.st_dev != expected.st_dev
            or current.st_ino != expected.st_ino
            or stat.S_IFMT(current.st_mode) != stat.S_IFMT(expected.st_mode)
        ):
            result.errors.append(f"{display_path}: changed; skipped")
            return None
        try:
            os.unlink(name, dir_fd=directory_fd)
        except OSError as exc:
            result.errors.append(f"{display_path}: {exc.strerror or exc}")
            return None
        return current

    def _remove_contents_fd(
        self,
        directory_fd: int,
        root_dev: int,
        root_mount_id: int,
        result: RemovalResult,
        *,
        display_path: str,
        top: bool = False,
    ) -> None:
        try:
            dir_stat = os.fstat(directory_fd)
        except OSError as exc:
            result.errors.append(f"{display_path}: {exc.strerror or exc}")
            return
        if not stat.S_ISDIR(dir_stat.st_mode):
            result.errors.append(f"{display_path}: is no longer a directory")
            return
        if dir_stat.st_dev != root_dev:
            result.errors.append(
                f"{display_path}: crossed a filesystem boundary; skipped"
            )
            return

        try:
            iterator = os.scandir(directory_fd)
        except OSError as exc:
            result.errors.append(f"{display_path}: {exc.strerror or exc}")
            return

        with iterator:
            for entry in iterator:
                name = entry.name
                child_path = os.path.join(display_path, name)
                try:
                    child_stat = os.stat(
                        name, dir_fd=directory_fd, follow_symlinks=False
                    )
                except OSError as exc:
                    result.errors.append(f"{child_path}: {exc.strerror or exc}")
                    continue

                if stat.S_ISLNK(child_stat.st_mode):
                    removed = self._unlink_verified_entry(
                        directory_fd, name, child_stat, child_path, result
                    )
                    if removed is not None:
                        result.removed_files += 1
                        result.bytes_removed += removed.st_size
                    continue

                if stat.S_ISDIR(child_stat.st_mode):
                    if child_stat.st_dev != root_dev:
                        result.errors.append(
                            f"{child_path}: is a separate filesystem; skipped"
                        )
                        continue
                    child_fd = -1
                    try:
                        try:
                            child_fd = os.open(
                                name,
                                self._directory_flags(),
                                dir_fd=directory_fd,
                            )
                        except OSError as exc:
                            result.errors.append(
                                f"{child_path}: cannot safely enter directory: "
                                f"{exc.strerror or exc}"
                            )
                            continue
                        opened = os.fstat(child_fd)
                        if (
                            not stat.S_ISDIR(opened.st_mode)
                            or opened.st_dev != child_stat.st_dev
                            or opened.st_ino != child_stat.st_ino
                        ):
                            result.errors.append(
                                f"{child_path}: changed while being opened; skipped"
                            )
                            continue
                        child_mount_id = self._mount_id_for_fd(child_fd)
                        if child_mount_id is None:
                            result.errors.append(
                                f"{child_path}: cannot verify mount boundary; "
                                "skipped"
                            )
                            continue
                        if child_mount_id != root_mount_id:
                            result.errors.append(
                                f"{child_path}: is a mount boundary; skipped"
                            )
                            continue
                        self._remove_contents_fd(
                            child_fd,
                            root_dev,
                            root_mount_id,
                            result,
                            display_path=child_path,
                        )
                    finally:
                        if child_fd >= 0:
                            os.close(child_fd)

                    try:
                        after = os.stat(
                            name, dir_fd=directory_fd, follow_symlinks=False
                        )
                    except OSError as exc:
                        result.errors.append(f"{child_path}: {exc.strerror or exc}")
                        continue
                    if (
                        not stat.S_ISDIR(after.st_mode)
                        or after.st_dev != child_stat.st_dev
                        or after.st_ino != child_stat.st_ino
                    ):
                        result.errors.append(f"{child_path}: changed; skipped")
                        continue
                    try:
                        os.rmdir(name, dir_fd=directory_fd)
                        result.removed_dirs += 1
                    except OSError as exc:
                        result.errors.append(f"{child_path}: {exc.strerror or exc}")
                    continue

                if stat.S_ISREG(child_stat.st_mode):
                    removed = self._unlink_verified_entry(
                        directory_fd, name, child_stat, child_path, result
                    )
                    if removed is not None:
                        result.removed_files += 1
                        result.bytes_removed += removed.st_size
                    continue

                # Sockets, device nodes, and FIFOs inside user caches are
                # unusual. Leave them in place and report them.
                result.skipped.append(child_path)

        if top:
            return


def _norm(path: str) -> str:
    return os.path.normpath(os.path.abspath(os.path.expanduser(path)))


def _sorted_unique_roots(paths: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        if not raw:
            continue
        path = _norm(raw)
        if path not in seen:
            seen.add(path)
            normalized.append(path)
    # If /home/user is an allowed root, /home/user/.cache is also allowed.
    normalized.sort(key=len)
    return normalized


def make_authorization(
    cleanup_items: Iterable,
    personal_files: Iterable[FileSnapshot],
    *,
    permanent_personal: bool,
) -> Authorization:
    return Authorization(
        cleanup_keys=frozenset(item.key for item in cleanup_items),
        personal_paths=frozenset(file.path for file in personal_files),
        permanent_personal=permanent_personal,
    )


def authorization_covers(
    authorization: Authorization,
    cleanup_items: Iterable,
    personal_files: Iterable[FileSnapshot],
    *,
    permanent_personal: bool,
) -> tuple[bool, str]:
    expected_keys = {item.key for item in cleanup_items}
    expected_paths = {file.path for file in personal_files}
    if not expected_keys.issubset(authorization.cleanup_keys):
        return False, "confirmation does not cover every selected cleanup item"
    if not expected_paths.issubset(authorization.personal_paths):
        return False, "confirmation does not cover every selected personal file"
    if permanent_personal and not authorization.permanent_personal:
        return False, "permanent deletion was not separately confirmed"
    return True, ""
