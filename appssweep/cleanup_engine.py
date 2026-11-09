"""Allowlisted, confirmed cleanup execution.

There is no dynamic command construction from user paths and no shell. Every
action is validated against a strict argument shape immediately before it
runs. Personal files are re-checked against their scan snapshot and moved to
Trash by default; permanent deletion needs a separate authorization bit.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path

from .models import (
    ActionKind,
    Authorization,
    CleanupItem,
    CleanupPlan,
    ExecutionReport,
    FileSnapshot,
    OperationResult,
)
from .package_manager import running_kernel_release
from .runner import CommandRunner
from .safety import (
    SafetyError,
    SafetyPolicy,
    authorization_covers,
)
from .util import format_command

ACTION_TIMEOUTS = {
    ActionKind.DNF_CLEAN_CACHE: 180,
    ActionKind.DNF_AUTOREMOVE: 600,
    ActionKind.DNF_OLD_KERNELS: 600,
    ActionKind.FLATPAK_UNUSED: 600,
    ActionKind.JOURNAL_VACUUM: 300,
    ActionKind.EMPTY_TRASH: 300,
    ActionKind.RPM_OSTREE_METADATA: 300,
}


class CleanupEngine:
    def __init__(
        self,
        policy: SafetyPolicy,
        runner: CommandRunner | None = None,
        *,
        filesystem_path: str | None = None,
    ) -> None:
        self.policy = policy
        self.runner = runner or CommandRunner()
        self.filesystem_path = filesystem_path or str(Path.home())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def execute_plan(
        self,
        plan: CleanupPlan,
        authorization: Authorization,
    ) -> ExecutionReport:
        ok, reason = authorization_covers(
            authorization,
            plan.cleanup_items,
            plan.personal_files,
            permanent_personal=plan.permanent_personal,
        )
        if not ok:
            raise PermissionError(f"Cleanup refused: {reason}.")
        if plan.is_empty:
            raise ValueError("Cleanup plan is empty.")

        _check_duplicates(plan)
        for item in plan.cleanup_items:
            if not item.eligible:
                raise PermissionError(
                    f"Cleanup item '{item.title}' is not eligible for automatic cleanup."
                )
            if item.action in {
                ActionKind.REPORT_ONLY,
                ActionKind.TRASH_PERSONAL,
                ActionKind.PERMANENT_PERSONAL,
            }:
                raise PermissionError(
                    f"Cleanup item '{item.title}' uses an action not valid for a "
                    "category item."
                )
        for file in plan.personal_files:
            validation = self.policy.validate_personal_snapshot(file)
            if not validation.ok:
                raise PermissionError(
                    f"Personal file validation failed for {file.path}: {validation.reason}."
                )

        report = ExecutionReport(
            filesystem_path=self.filesystem_path,
            available_before=self._available(),
        )
        for item in plan.cleanup_items:
            report.results.append(self._execute_cleanup_item(item))

        if plan.personal_files:
            if plan.permanent_personal:
                for file in plan.personal_files:
                    report.results.append(self._delete_personal_permanently(file))
            else:
                report.results.extend(self._move_personal_to_trash(plan.personal_files))

        report.available_after = self._available()
        return report

    # ------------------------------------------------------------------
    # Category operations
    # ------------------------------------------------------------------
    def _execute_cleanup_item(self, item: CleanupItem) -> OperationResult:
        try:
            if item.action == ActionKind.CLEAR_CACHE_CONTENTS:
                return self._clear_cache_item(item)
            return self._run_command_item(item)
        except SafetyError as exc:
            return OperationResult(
                title=item.title,
                success=False,
                message=str(exc),
                needs_admin=item.needs_admin,
            )
        except Exception as exc:  # repository boundary: report, never crash midway  # noqa: BLE001
            return OperationResult(
                title=item.title,
                success=False,
                message=f"Unexpected cleanup failure: {exc}",
                needs_admin=item.needs_admin,
            )

    def _clear_cache_item(self, item: CleanupItem) -> OperationResult:
        if not item.paths:
            return OperationResult(
                title=item.title,
                success=False,
                message="No cache path was recorded for this item.",
            )
        total_bytes = 0
        removed_files = 0
        errors: list[str] = []
        for path in item.paths:
            try:
                result = self.policy.clear_cache_target(path)
                total_bytes += result.bytes_removed
                removed_files += result.removed_files
                errors.extend(result.errors)
            except SafetyError as exc:
                errors.append(f"{path}: {exc}")
        command = ""
        success = not errors and (removed_files > 0 or total_bytes > 0)
        message = (
            f"Cleared {removed_files} cache file(s); the folder itself was kept. "
            "Apps may rebuild these caches."
        )
        if errors:
            message += " Some files could not be removed: " + "; ".join(errors[:4])
            if len(errors) > 4:
                message += f" (+{len(errors) - 4} more)"
        return OperationResult(
            title=item.title,
            success=success,
            reclaimed_bytes=total_bytes,
            message=message,
            command=command,
            needs_admin=False,
        )

    def _run_command_item(self, item: CleanupItem) -> OperationResult:
        if not item.command_args:
            return OperationResult(
                title=item.title,
                success=False,
                message="No allowlisted command was recorded for this item.",
                needs_admin=item.needs_admin,
            )
        validation_error = validate_item_command(item)
        if validation_error:
            return OperationResult(
                title=item.title,
                success=False,
                message=f"Command safety check failed: {validation_error}",
                command=" ".join(item.command_args[0]) if item.command_args else "",
                needs_admin=item.needs_admin,
            )

        timeout = ACTION_TIMEOUTS.get(item.action, 300)
        outputs: list[str] = []
        all_ok = True
        needs_admin = item.needs_admin
        commands_display: list[str] = []
        for args in item.command_args:
            result = self.runner.run(
                args,
                needs_admin=needs_admin,
                timeout=timeout,
            )
            commands_display.append(result.command or format_command(args))
            outputs.append(result.output)
            if not result.ok:
                all_ok = False
                break

        message = _short_output(outputs)
        if all_ok:
            message = (
                message
                or "System command completed. Actual disk reclaim is shown by the "
                "filesystem availability change."
            )
            message += (
                " AppSweep does not estimate command reclaim as exact; the filesystem "
                "summary is authoritative."
            )
        else:
            if not message:
                message = "System command failed."
        return OperationResult(
            title=item.title,
            success=all_ok,
            reclaimed_bytes=0,
            message=message,
            command="\n".join(commands_display),
            needs_admin=needs_admin,
        )

    # ------------------------------------------------------------------
    # Personal files
    # ------------------------------------------------------------------
    def _move_personal_to_trash(
        self, files: list[FileSnapshot]
    ) -> list[OperationResult]:
        gio = self.runner.find("gio")
        results: list[OperationResult] = []
        if not gio:
            for file in files:
                results.append(
                    OperationResult(
                        title=file.filename,
                        success=False,
                        message=(
                            "gio is not available. AppSweep will not permanently "
                            "delete the file as a fallback."
                        ),
                    )
                )
            return results

        for file in files:
            validation = self.policy.validate_personal_snapshot(file)
            if not validation.ok:
                results.append(
                    OperationResult(
                        title=file.path,
                        success=False,
                        message=f"Revalidation failed; file was not moved: {validation.reason}",
                    )
                )
                continue
            result = self.runner.run(
                [gio, "trash", "--", file.path],
                timeout=120,
            )
            if result.ok:
                results.append(
                    OperationResult(
                        title=file.filename,
                        success=True,
                        reclaimed_bytes=0,
                        message=(
                            "Moved to Trash. This does not free disk space until Trash "
                            "is emptied."
                        ),
                        command=result.command,
                    )
                )
            else:
                output = result.output.strip() or "gio trash failed"
                results.append(
                    OperationResult(
                        title=file.filename,
                        success=False,
                        message=(
                            f"Could not move to Trash: {output}. File left untouched; "
                            "AppSweep did not fall back to permanent deletion."
                        ),
                        command=result.command,
                    )
                )
        return results

    def _delete_personal_permanently(self, file: FileSnapshot) -> OperationResult:
        try:
            result = self.policy.remove_personal_file(file)
        except SafetyError as exc:
            return OperationResult(
                title=file.filename,
                success=False,
                message=f"Permanent deletion refused or failed: {exc}",
            )
        return OperationResult(
            title=file.filename,
            success=True,
            reclaimed_bytes=result.bytes_removed,
            message="Permanently deleted after the extra confirmation.",
            command=f"Validated unlink of {file.path}",
        )

    # ------------------------------------------------------------------
    def _available(self) -> int:
        try:
            stats = os.statvfs(self.filesystem_path)
            return stats.f_bavail * stats.f_frsize
        except OSError:
            return 0


def _check_duplicates(plan: CleanupPlan) -> None:
    keys = [item.key for item in plan.cleanup_items]
    if len(keys) != len(set(keys)):
        raise ValueError("Cleanup plan contains duplicate cleanup items.")
    paths = [file.path for file in plan.personal_files]
    if len(paths) != len(set(paths)):
        raise ValueError("Cleanup plan contains duplicate personal files.")


def _check_executable(args: tuple[str, ...], allowed_names: set[str]) -> str | None:
    if not args:
        return "command is empty"
    name = os.path.basename(args[0])
    if name not in allowed_names:
        return f"executable {name!r} is not allowlisted"
    if "\x00" in " ".join(args):
        return "command contains a NUL byte"
    return None


def validate_item_command(item: CleanupItem) -> str | None:
    """Validate the exact argument shape for an allowlisted action."""

    for args in item.command_args:
        args = tuple(str(arg) for arg in args)
        if item.action == ActionKind.DNF_CLEAN_CACHE:
            problem = _check_executable(args, {"dnf", "dnf5"})
            if problem:
                return problem
            if len(args) != 3 or args[1] != "clean" or args[2] not in {
                "all",
                "packages",
                "metadata",
            }:
                return "DNF cache cleanup must be exactly 'clean all|packages|metadata'"

        elif item.action == ActionKind.DNF_AUTOREMOVE:
            problem = _check_executable(args, {"dnf", "dnf5"})
            if problem:
                return problem
            if "autoremove" not in args:
                return "autoremove action must call DNF autoremove"
            if not any(arg in {"-y", "--assumeyes"} for arg in args):
                return "autoremove must run non-interactively after AppSweep confirmation"
            current = running_kernel_release()
            current_norm = current.rsplit(".", 1)[0] if current else ""
            exclude_args = [arg for arg in args if arg.startswith("--exclude=")]
            if not exclude_args:
                return "autoremove must explicitly exclude the running kernel"
            if current_norm and not any(current_norm in arg for arg in exclude_args):
                return "autoremove exclusions do not cover the running kernel"
            preview_names = set(item.metadata.get("package_names") or [])
            if any(name.startswith("kernel") for name in preview_names):
                return "autoremove preview included a kernel package"

        elif item.action == ActionKind.DNF_OLD_KERNELS:
            problem = _check_executable(args, {"dnf", "dnf5"})
            if problem:
                return problem
            if "remove" not in args:
                return "old-kernel action must call DNF remove"
            if not any(arg in {"-y", "--assumeyes"} for arg in args):
                return "old-kernel removal must run non-interactively"
            current = running_kernel_release()
            operands = [arg for arg in args[2:] if not arg.startswith("-")]
            if not operands:
                return "old-kernel action has no package operands"
            for operand in operands:
                if not operand.startswith(("kernel-", "kernel.")):
                    return f"old-kernel action contains a non-kernel package: {operand}"
                if current and current in operand:
                    return "old-kernel action includes the currently running kernel"
            versions = item.metadata.get("versions") or []
            if current and any(current in version for version in versions):
                return "old-kernel metadata includes the currently running kernel"

        elif item.action == ActionKind.FLATPAK_UNUSED:
            problem = _check_executable(args, {"flatpak"})
            if problem:
                return problem
            if "uninstall" not in args or "--unused" not in args:
                return "Flatpak action must be uninstall --unused"
            if not any(arg in {"-y", "--assumeyes"} for arg in args):
                return "Flatpak unused cleanup must run non-interactively"
            forbidden = {"--all", "--delete-data", "--force-remove"}
            if any(arg in forbidden for arg in args):
                return "Flatpak action contains a forbidden destructive option"
            try:
                uninstall_index = args.index("uninstall")
            except ValueError:
                return "Flatpak uninstall subcommand missing"
            after = args[uninstall_index + 1 :]
            for token in after:
                if not token.startswith("-"):
                    return f"Flatpak action contains an explicit ref {token!r}; only --unused is allowed"
            install_flags = [
                flag
                for flag in list(after) + list(args[:uninstall_index])
                if flag in {"--user", "--system"}
            ]
            if len(install_flags) > 1:
                return "Flatpak action must target exactly one installation"
            if "--system" in args and not item.needs_admin:
                return "system Flatpak cleanup must request administrator access"

        elif item.action == ActionKind.JOURNAL_VACUUM:
            problem = _check_executable(args, {"journalctl"})
            if problem:
                return problem
            if len(args) != 2 or not re.fullmatch(
                r"--vacuum-(?:size|time)=[0-9A-Za-z.+-]+", args[1]
            ):
                return "journal cleanup must be exactly one journalctl --vacuum-size/time operation"

        elif item.action == ActionKind.EMPTY_TRASH:
            problem = _check_executable(args, {"gio"})
            if problem:
                return problem
            if args[1:] != ("trash", "--empty"):
                return "Trash action must be exactly gio trash --empty"

        elif item.action == ActionKind.RPM_OSTREE_METADATA:
            problem = _check_executable(args, {"rpm-ostree"})
            if problem:
                return problem
            if args[1:] not in {("cleanup", "-m"), ("cleanup", "--metadata")}:
                return "rpm-ostree action must be metadata-only cleanup"

        else:
            return f"action {item.action.value!r} is not executable by AppSweep"
    return None


def _short_output(outputs: Iterable[str], limit: int = 500) -> str:
    text = "\n".join(output.strip() for output in outputs if output and output.strip())
    if not text:
        return ""
    lines = [line for line in text.splitlines() if line.strip()]
    compact = "\n".join(lines[-10:])
    return compact[:limit]
