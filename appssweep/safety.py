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
        return Validation(True, path=normalized)

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
        validation = self.validate_personal_snapshot(snapshot)
        if not validation.ok:
            raise SafetyError(validation.reason)
        result = RemovalResult()
        try:
            lstat = os.lstat(validation.path)
            if lstat.st_size != snapshot.size or lstat.st_ino != snapshot.st_ino:
                raise SafetyError("file changed immediately before deletion")
            os.unlink(validation.path)
        except SafetyError:
            raise
        except OSError as exc:
            raise SafetyError(f"could not delete {validation.path}: {exc}") from exc
        result.removed_files = 1
        result.bytes_removed = snapshot.size
        return result

    def clear_cache_target(self, path: str) -> RemovalResult:
        """Remove a cache target or its contents without following symlinks.

        The target directory itself is preserved. Filesystems inside a cache
        directory are never crossed.
        """

        validation = self.validate_cache_target(path)
        if not validation.ok:
            raise SafetyError(validation.reason)
        normalized = validation.path
        result = RemovalResult()
        try:
            lstat = os.lstat(normalized)
        except OSError as exc:
            raise SafetyError(f"cannot inspect cache target: {exc}") from exc

        if stat.S_ISREG(lstat.st_mode):
            try:
                os.unlink(normalized)
            except OSError as exc:
                raise SafetyError(f"could not remove cache file: {exc}") from exc
            result.removed_files = 1
            result.bytes_removed = lstat.st_size
            return result

        if not stat.S_ISDIR(lstat.st_mode):
            raise SafetyError("cache target is not a regular file or folder")
        self._remove_contents(normalized, lstat.st_dev, result, top=True)
        return result

    def _remove_contents(
        self,
        directory: str,
        root_dev: int,
        result: RemovalResult,
        *,
        top: bool = False,
    ) -> None:
        # Re-check each directory before entering it. This is intentionally
        # done per directory, not once for the whole tree.
        try:
            dir_lstat = os.lstat(directory)
        except OSError as exc:
            result.errors.append(f"{directory}: {exc}")
            return
        if stat.S_ISLNK(dir_lstat.st_mode):
            result.errors.append(f"{directory}: became a symbolic link; skipped")
            return
        if not stat.S_ISDIR(dir_lstat.st_mode):
            result.errors.append(f"{directory}: is no longer a directory")
            return
        if dir_lstat.st_dev != root_dev:
            result.errors.append(f"{directory}: crossed a filesystem boundary; skipped")
            return
        try:
            with os.scandir(directory) as iterator:
                entries = list(iterator)
        except OSError as exc:
            result.errors.append(f"{directory}: {exc.strerror or exc}")
            return

        for entry in entries:
            try:
                lstat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                result.errors.append(f"{entry.path}: {exc.strerror or exc}")
                continue
            if stat.S_ISLNK(lstat.st_mode):
                try:
                    os.unlink(entry.path)
                    result.removed_files += 1
                    result.bytes_removed += lstat.st_size
                except OSError as exc:
                    result.errors.append(f"{entry.path}: {exc.strerror or exc}")
                continue
            if stat.S_ISDIR(lstat.st_mode):
                if lstat.st_dev != root_dev:
                    result.errors.append(
                        f"{entry.path}: is a separate filesystem; skipped"
                    )
                    continue
                self._remove_contents(entry.path, root_dev, result)
                try:
                    os.rmdir(entry.path)
                    result.removed_dirs += 1
                except OSError as exc:
                    result.errors.append(f"{entry.path}: {exc.strerror or exc}")
                continue
            if stat.S_ISREG(lstat.st_mode):
                try:
                    os.unlink(entry.path)
                    result.removed_files += 1
                    result.bytes_removed += lstat.st_size
                except OSError as exc:
                    result.errors.append(f"{entry.path}: {exc.strerror or exc}")
                continue
            # Sockets, device nodes, and FIFOs inside user caches are unusual.
            # Leave them in place and report them rather than removing blindly.
            result.skipped.append(entry.path)

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
