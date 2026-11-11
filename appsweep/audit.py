"""Private, append-only cleanup audit logging.

Audit records are written only after a plan has been explicitly confirmed and
immediately before/after execution. The log is local state, not a scan report:
it exists so a person can later see exactly which cleanup plan was authorized
and what happened.

The file is created with mode 0600 inside a 0700 ``appsweep`` state directory.
Symlinks, non-regular files, foreign-owned directories, and group/world-writable
state directories are refused rather than followed.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

from . import APP_NAME, __version__
from .models import Authorization, CleanupPlan, ExecutionReport
from .util import now_iso

AUDIT_DIR_NAME = "appsweep"
AUDIT_FILE_NAME = "cleanup-audit.log"


def audit_log_path(home: str | None = None) -> str:
    """Return the default user audit-log path without creating it."""

    state_home = os.environ.get("XDG_STATE_HOME", "").strip()
    if state_home:
        base = os.path.abspath(os.path.expanduser(state_home))
    else:
        base = os.path.join(
            os.path.abspath(os.path.expanduser(home or str(Path.home()))),
            ".local",
            "state",
        )
    return os.path.join(base, AUDIT_DIR_NAME, AUDIT_FILE_NAME)


def append_cleanup_event(
    event: str,
    plan: CleanupPlan,
    authorization: Authorization,
    *,
    home: str | None = None,
    execution: ExecutionReport | None = None,
    error: str = "",
    log_path: str | None = None,
) -> str | None:
    """Append one JSON-line audit record.

    Returns ``None`` on success or a clear error string on failure. Audit-log
    failures must not hide a successful cleanup result, so callers report the
    error as a warning instead of raising.
    """

    path = os.path.abspath(os.path.expanduser(log_path or audit_log_path(home)))
    parent = os.path.dirname(path)
    file_name = os.path.basename(path)
    if (
        not file_name
        or file_name in {".", ".."}
        or os.sep in file_name
        or (os.altsep and os.altsep in file_name)
    ):
        return "cleanup audit log path has an unsafe file name"

    parent_fd, parent_error = _open_private_parent(parent)
    if parent_error:
        return parent_error
    assert parent_fd is not None

    record: dict[str, Any] = {
        "timestamp": now_iso(),
        "event": event,
        "app": APP_NAME,
        "app_version": __version__,
        "uid": os.getuid(),
        "plan": _plan_record(plan, authorization),
        "error": error,
    }
    if execution is not None:
        record["execution"] = execution.to_dict()

    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOCTTY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        try:
            fd = os.open(file_name, flags, 0o600, dir_fd=parent_fd)
        except OSError as exc:
            return f"could not open the cleanup audit log: {exc.strerror or exc}"

        try:
            stat_result = os.fstat(fd)
            if not stat.S_ISREG(stat_result.st_mode):
                return "cleanup audit log is not a regular file"
            if stat_result.st_uid != os.getuid():
                return "cleanup audit log is owned by another user"
            if stat_result.st_nlink != 1:
                return "cleanup audit log has multiple hard links"
            try:
                os.fchmod(fd, 0o600)
                payload = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                encoded = payload.encode("utf-8")
                written = os.write(fd, encoded)
                if written != len(encoded):
                    return "cleanup audit log write was incomplete"
                os.fsync(fd)
            except OSError as exc:
                return f"could not write the cleanup audit log: {exc.strerror or exc}"
        finally:
            os.close(fd)
    finally:
        os.close(parent_fd)
    return None


def _open_private_parent(parent: str) -> tuple[int | None, str | None]:
    """Create/open the audit directory and enforce mode 0700.

    The directory is opened by descriptor so the later audit-file operation is
    anchored to the same directory object. Existing group/world-writable
    directories are refused; existing 0755/0711 directories are tightened to
    0700 before any record is written.
    """

    try:
        os.makedirs(parent, mode=0o700, exist_ok=True)
    except OSError as exc:
        return None, f"could not create the cleanup audit folder: {exc.strerror or exc}"

    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        return None, "cleanup audit folder cannot be opened without symlink following"

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(parent, flags)
    except OSError as exc:
        return None, f"could not open the cleanup audit folder: {exc.strerror or exc}"

    success = False
    try:
        parent_stat = os.fstat(fd)
        if not stat.S_ISDIR(parent_stat.st_mode):
            return None, "cleanup audit folder is not a real directory"
        if parent_stat.st_uid != os.getuid():
            return None, "cleanup audit folder is owned by another user"
        if parent_stat.st_mode & 0o022:
            return None, "cleanup audit folder is writable by another user"
        if stat.S_IMODE(parent_stat.st_mode) != 0o700:
            try:
                os.fchmod(fd, 0o700)
            except OSError as exc:
                return None, (
                    "could not enforce private 0700 permissions on the cleanup "
                    f"audit folder: {exc.strerror or exc}"
                )
            parent_stat = os.fstat(fd)
            if stat.S_IMODE(parent_stat.st_mode) != 0o700:
                return None, (
                    "cleanup audit folder permissions could not be verified as 0700"
                )
        success = True
        return fd, None
    except OSError as exc:
        return None, f"could not inspect the cleanup audit folder: {exc.strerror or exc}"
    finally:
        if not success:
            try:
                os.close(fd)
            except OSError:
                pass


def _plan_record(plan: CleanupPlan, authorization: Authorization) -> dict[str, Any]:
    return {
        "description": plan.describe(),
        "permanent_personal": plan.permanent_personal,
        "cleanup_total": plan.cleanup_total,
        "personal_total": plan.personal_total,
        "total": plan.total,
        "cleanup_items": [
            {
                "key": item.key,
                "title": item.title,
                "group": item.group.value,
                "size_bytes": item.size_bytes,
                "action": item.action.value,
                "needs_admin": item.needs_admin,
                "paths": list(item.paths),
                "commands": [list(args) for args in item.command_args],
            }
            for item in plan.cleanup_items
        ],
        "personal_files": [
            {
                "path": file.path,
                "filename": file.filename,
                "size_bytes": file.size,
            }
            for file in plan.personal_files
        ],
        "authorization": {
            "cleanup_keys": sorted(authorization.cleanup_keys),
            "personal_paths": sorted(authorization.personal_paths),
            "permanent_personal": authorization.permanent_personal,
        },
    }
