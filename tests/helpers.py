"""Shared test helpers for AppSweep."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from appsweep.filetypes import classify_path
from appsweep.models import FileSnapshot, RiskGroup


def write_file(path: Path, size: int, byte: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(byte * size)
    return path


def snapshot_for(path: Path) -> FileSnapshot:
    lstat = os.lstat(path)
    file_type = classify_path(str(path))
    return FileSnapshot(
        path=os.path.normpath(os.path.abspath(str(path))),
        size=lstat.st_size,
        allocated=max(0, int(getattr(lstat, "st_blocks", 0)) * 512),
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


def make_unreadable(path: Path) -> None:
    os.chmod(path, 0)


def restore_readable(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IRWXU)
    except OSError:
        pass


class FakeRunner:
    """A small CommandRunner-compatible test double."""

    def __init__(self, mapping=None, handler=None, euid: int = 1000) -> None:
        self.mapping = dict(mapping or {})
        self.handler = handler
        self.euid = euid
        self.calls: list[dict] = []
        self.which_calls: list[str] = []

    def find(self, name: str):
        self.which_calls.append(name)
        return self.mapping.get(name)

    def available(self, name: str) -> bool:
        return self.find(name) is not None

    def build_command(self, args, *, needs_admin: bool = False):
        argv = tuple(str(arg) for arg in args)
        if needs_admin and self.euid != 0:
            pkexec = self.find("pkexec")
            if not pkexec:
                raise RuntimeError("pkexec is not available")
            return (pkexec, *argv)
        return argv

    def run(self, args, *, needs_admin: bool = False, timeout: int = 60, **kwargs):
        from appsweep.runner import CommandResult

        self.calls.append(
            {
                "args": tuple(str(arg) for arg in args),
                "needs_admin": needs_admin,
                "timeout": timeout,
                **kwargs,
            }
        )
        if self.handler is None:
            return CommandResult(args=tuple(str(arg) for arg in args), returncode=0)
        return self.handler(tuple(str(arg) for arg in args), needs_admin, timeout)
