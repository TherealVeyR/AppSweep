"""Filesystem discovery, boundaries, and read-only size measurement."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .models import FilesystemInfo, MountInfo, PermissionIssue, SkippedPath
from .util import is_within

VIRTUAL_FSTYPES = {
    "proc",
    "sysfs",
    "devtmpfs",
    "devpts",
    "cgroup",
    "cgroup2",
    "tmpfs",
    "ramfs",
    "securityfs",
    "pstore",
    "debugfs",
    "tracefs",
    "configfs",
    "fusectl",
    "hugetlbfs",
    "mqueue",
    "bpf",
    "autofs",
    "binfmt_misc",
    "nsfs",
    "overlay",
    "squashfs",
    "erofs",
    "composefs",
    "portal",
}

NETWORK_FSTYPES = {
    "nfs",
    "nfs4",
    "cifs",
    "smb3",
    "smbfs",
    "sshfs",
    "fuse.sshfs",
    "fuse.rclone",
    "ceph",
    "glusterfs",
    "davfs",
    "afs",
    "9p",
}

LOCAL_FSTYPES = {
    "btrfs",
    "ext2",
    "ext3",
    "ext4",
    "xfs",
    "f2fs",
    "vfat",
    "exfat",
    "ntfs",
    "ntfs3",
    "jfs",
    "reiserfs",
    "zfs",
}

# Read-only image filesystems used by immutable Fedora deployments. They are
# local storage even though they are not in the read/write local set above.
READ_ONLY_IMAGE_FSTYPES = {
    "squashfs",
    "erofs",
    "composefs",
}


# Filesystem paths that should never be traversed by a storage scan.
BASE_EXCLUDES = (
    "/proc",
    "/sys",
    "/dev",
    "/run",
    "/lost+found",
    "/var/lib/systemd/coredump",  # reported separately, not traversed as personal data
)


def _decode_mount_field(value: str) -> str:
    return re.sub(
        r"\\([0-7]{3})",
        lambda match: chr(int(match.group(1), 8)),
        value,
    )


def read_mountinfo(path: str = "/proc/self/mountinfo") -> list[MountInfo]:
    """Read Linux mount information without invoking a subprocess."""

    mounts: list[MountInfo] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return mounts

    for line in lines:
        fields = line.split()
        if len(fields) < 7:
            continue
        try:
            separator = fields.index("-")
        except ValueError:
            continue
        if separator + 2 >= len(fields):
            continue
        try:
            mount_point = _decode_mount_field(fields[4])
            options = fields[5]
            major_minor = fields[2]
            fstype = _decode_mount_field(fields[separator + 1])
            source = _decode_mount_field(fields[separator + 2])
        except (IndexError, ValueError):
            continue
        mounts.append(
            MountInfo(
                mount_point=os.path.normpath(mount_point),
                source=source,
                fstype=fstype,
                options=options,
                major_minor=major_minor,
            )
        )
    return mounts


def is_flatpak_sandbox() -> bool:
    return os.path.exists("/.flatpak-info")


def is_network_filesystem(fstype: str) -> bool:
    return fstype.lower() in NETWORK_FSTYPES or fstype.lower().startswith("fuse.ssh")


def is_local_filesystem(fstype: str) -> bool:
    fs = fstype.lower()
    return fs in LOCAL_FSTYPES or fs.startswith("fuse.") and fs not in NETWORK_FSTYPES


def mount_for_path(path: str, mounts: Iterable[MountInfo]) -> MountInfo | None:
    normalized = os.path.normpath(os.path.abspath(path))
    candidates = [
        mount
        for mount in mounts
        if is_within(normalized, mount.mount_point)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda mount: len(Path(mount.mount_point).parts))


def discover_installation_roots(
    home: str | None = None,
    *,
    sandboxed: bool | None = None,
    mounts: Iterable[MountInfo] | None = None,
) -> tuple[list[str], list[str], list[MountInfo]]:
    """Return safe roots for a full installation scan plus explanatory notes.

    The root filesystem always comes from the live mount table. Commonly
    split Fedora filesystems (/home, /var, /usr, /boot, /opt, /etc, /root,
    /srv, and disk-backed /tmp) are included only when they are separate local
    filesystems. A read-only /usr image on an immutable Fedora system is
    included for storage analysis. External and network mounts are not
    included automatically.
    """

    home = os.path.abspath(os.path.expanduser(home or str(Path.home())))
    if sandboxed is None:
        sandboxed = is_flatpak_sandbox()
    mount_list = list(mounts) if mounts is not None else read_mountinfo()
    notes: list[str] = []

    if sandboxed:
        notes.append(
            "Running inside a Flatpak sandbox: the host root filesystem and "
            "package tools are not directly reachable. Only accessible local "
            "folders are scanned."
        )
        roots = [home] if os.path.isdir(home) else []
        return roots, notes, mount_list

    try:
        root_dev = os.stat("/").st_dev
    except OSError:
        root_dev = 0
    roots = ["/"]
    home_mount = mount_for_path(home, mount_list)
    if home_mount and home_mount.fstype.lower() in VIRTUAL_FSTYPES:
        notes.append(
            f"Home ({home}) is on a virtual filesystem ({home_mount.fstype}); "
            "it is still scanned because it is explicitly your home folder."
        )
        roots.append(home)
    elif home_mount and home_mount.mount_point != "/":
        try:
            home_dev = os.stat(home_mount.mount_point).st_dev
        except OSError:
            home_dev = -1
        if home_dev != root_dev and is_local_filesystem(home_mount.fstype):
            roots.append(home_mount.mount_point)
            notes.append(
                f"/home is a separate local {home_mount.fstype} filesystem and is included."
            )

    for candidate in ("/var", "/usr", "/boot", "/opt", "/etc", "/root", "/srv", "/tmp"):
        if not os.path.isdir(candidate):
            continue
        mount = mount_for_path(candidate, mount_list)
        if not mount or mount.mount_point != candidate:
            continue
        try:
            candidate_dev = os.stat(candidate).st_dev
        except OSError:
            continue
        already_covered = candidate_dev == root_dev and not any(
            is_within(candidate, existing) and existing != "/" for existing in roots
        )
        if already_covered:
            continue
        read_only_usr = (
            candidate == "/usr"
            and mount.fstype.lower() in READ_ONLY_IMAGE_FSTYPES
        )
        if read_only_usr:
            roots.append(candidate)
            notes.append(
                f"{candidate} is a read-only {mount.fstype} image and is included "
                "for storage analysis."
            )
            continue
        if mount.fstype.lower() in VIRTUAL_FSTYPES or is_network_filesystem(mount.fstype):
            notes.append(f"{candidate} is {mount.fstype}; skipped as a separate virtual/network mount.")
            continue
        if is_local_filesystem(mount.fstype):
            roots.append(candidate)
            notes.append(
                f"{candidate} is a separate local {mount.fstype} filesystem and is included."
            )

    # Remove overlaps conservatively: if a root is already inside another
    # root on the same device, scanning twice would double-count entries.
    cleaned: list[str] = []
    for root in roots:
        try:
            root_dev = os.stat(root).st_dev
        except OSError:
            continue
        duplicate = False
        for existing in cleaned:
            try:
                existing_dev = os.stat(existing).st_dev
            except OSError:
                continue
            if root != existing and is_within(root, existing) and root_dev == existing_dev:
                duplicate = True
                break
        if not duplicate:
            cleaned.append(root)

    return cleaned, notes, mounts


def filesystem_infos(roots: Iterable[str], mounts: Iterable[MountInfo]) -> list[FilesystemInfo]:
    """Build capacity information for the filesystems containing the roots."""

    mount_list = list(mounts)
    infos: list[FilesystemInfo] = []
    seen: set[str] = set()
    for root in roots:
        mount = mount_for_path(root, mount_list)
        target = mount.mount_point if mount else root
        if target in seen:
            continue
        try:
            stats = os.statvfs(target)
        except OSError:
            continue
        total = stats.f_blocks * stats.f_frsize
        available = stats.f_bavail * stats.f_frsize
        free = stats.f_bfree * stats.f_frsize
        used = max(0, total - free)
        infos.append(
            FilesystemInfo(
                path=target,
                source=mount.source if mount else "",
                fstype=mount.fstype if mount else "unknown",
                total=total,
                used=used,
                available=available,
                mount_options=mount.options if mount else "",
            )
        )
        seen.add(target)
    return infos


def is_mount_boundary(
    path: str,
    root_dev: int,
    mount_map: dict[str, MountInfo] | None = None,
) -> tuple[bool, str]:
    """Return whether path is a mount boundary that must not be entered."""

    try:
        path_dev = os.stat(path, follow_symlinks=False).st_dev
    except OSError:
        return True, "unreadable"
    if path_dev != root_dev:
        return True, "different filesystem"
    mount = (mount_map or {}).get(os.path.normpath(os.path.abspath(path)))
    if mount:
        if (
            mount.fstype.lower() in VIRTUAL_FSTYPES
            or is_network_filesystem(mount.fstype)
        ):
            return True, f"{mount.fstype} mount"
        return True, f"{mount.fstype} mount boundary"
    return False, ""


@dataclass
class TreeMeasurement:
    path: str
    logical_size: int = 0
    allocated_size: int = 0
    files: int = 0
    directories: int = 0
    symlinks: int = 0
    permission_errors: list[PermissionIssue] = field(default_factory=list)
    skipped: list[SkippedPath] = field(default_factory=list)
    cancelled: bool = False
    incomplete: bool = False

    @property
    def size(self) -> int:
        return self.logical_size

    @property
    def allocated(self) -> int:
        return self.allocated_size


def measure_path(
    path: str,
    *,
    excluded_paths: Iterable[str] = (),
    stay_on_filesystem: bool = True,
    mounts: Iterable[MountInfo] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    max_issues: int = 20,
) -> TreeMeasurement:
    """Measure a file or directory without following symlinks.

    This is the targeted counterpart to the full scanner, used for known
    cache/log/trash locations. It never reads file contents.
    """

    result = TreeMeasurement(path=os.path.abspath(path))
    try:
        root_lstat = os.lstat(result.path)
    except OSError as exc:
        result.permission_errors.append(PermissionIssue(result.path, str(exc)))
        result.incomplete = True
        return result

    if stat.S_ISLNK(root_lstat.st_mode):
        result.symlinks += 1
        result.permission_errors.append(
            PermissionIssue(result.path, "symbolic link targets are never followed")
        )
        result.incomplete = True
        return result

    excludes = [os.path.normpath(os.path.abspath(p)) for p in excluded_paths]
    mount_map = {os.path.normpath(m.mount_point): m for m in (mounts or [])}
    root_dev = root_lstat.st_dev
    seen_inodes: set[tuple[int, int]] = set()

    if stat.S_ISREG(root_lstat.st_mode):
        result.files = 1
        result.logical_size = root_lstat.st_size
        result.allocated_size = _allocated_size(root_lstat)
        return result

    if not stat.S_ISDIR(root_lstat.st_mode):
        result.files = 1
        result.logical_size = root_lstat.st_size
        result.allocated_size = _allocated_size(root_lstat)
        return result

    stack = [result.path]
    while stack:
        if cancel_check and cancel_check():
            result.cancelled = True
            result.incomplete = True
            break
        current = stack.pop()
        if _is_excluded(current, excludes):
            continue
        try:
            entries = list(os.scandir(current))
        except PermissionError as exc:
            result.incomplete = True
            if len(result.permission_errors) < max_issues:
                result.permission_errors.append(PermissionIssue(current, str(exc)))
            continue
        except OSError as exc:
            result.incomplete = True
            if len(result.permission_errors) < max_issues:
                result.permission_errors.append(PermissionIssue(current, str(exc)))
            continue

        result.directories += 1
        for entry in entries:
            if cancel_check and cancel_check():
                result.cancelled = True
                result.incomplete = True
                stack.clear()
                break
            try:
                lstat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                result.incomplete = True
                if len(result.permission_errors) < max_issues:
                    result.permission_errors.append(PermissionIssue(entry.path, str(exc)))
                continue

            if stat.S_ISLNK(lstat.st_mode):
                result.symlinks += 1
                continue
            if stat.S_ISDIR(lstat.st_mode):
                if _is_excluded(entry.path, excludes):
                    continue
                if stay_on_filesystem and lstat.st_dev != root_dev:
                    if len(result.skipped) < max_issues:
                        result.skipped.append(
                            SkippedPath(entry.path, "different filesystem", "")
                        )
                    continue
                mount = mount_map.get(os.path.normpath(entry.path))
                if mount:
                    if len(result.skipped) < max_issues:
                        if (
                            mount.fstype.lower() in VIRTUAL_FSTYPES
                            or is_network_filesystem(mount.fstype)
                        ):
                            reason = "virtual/network mount"
                        else:
                            reason = "nested mount boundary"
                        result.skipped.append(
                            SkippedPath(entry.path, reason, mount.fstype)
                        )
                    continue
                stack.append(entry.path)
                continue
            if not stat.S_ISREG(lstat.st_mode):
                continue

            identity = (lstat.st_dev, lstat.st_ino)
            if lstat.st_nlink > 1:
                if identity in seen_inodes:
                    continue
                seen_inodes.add(identity)
            result.files += 1
            result.logical_size += lstat.st_size
            result.allocated_size += _allocated_size(lstat)

    return result


def _is_excluded(path: str, excludes: Iterable[str]) -> bool:
    normalized = os.path.normpath(os.path.abspath(path))
    for excluded in excludes:
        if is_within(normalized, excluded):
            return True
    return False


def _allocated_size(stats: os.stat_result) -> int:
    blocks = getattr(stats, "st_blocks", None)
    if blocks is None:
        return int(stats.st_size)
    return max(0, int(blocks) * 512)


def path_is_within_mount(path: str, mounts: Iterable[MountInfo]) -> bool:
    return mount_for_path(path, mounts) is not None
