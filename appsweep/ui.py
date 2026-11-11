"""Terminal presentation for AppSweep.

The fallback is fully usable with only ANSI colours. Rich is optional: when it
is installed, it renders the same text with its own console handling. No part
of the report depends on colour, and all filesystem-derived strings are
escaped before printing so a hostile file name cannot inject terminal
commands.
"""

from __future__ import annotations

import os
import shutil
import sys
import textwrap
from collections.abc import Iterable
from pathlib import Path
from typing import TextIO

from .capabilities import VERIFICATION_NOTE, CapabilityReport
from .models import (
    CleanupItem,
    CleanupReport,
    FileSnapshot,
    ReportOnlyFinding,
    RiskGroup,
    ScanResult,
)
from .util import (
    human_bytes,
    human_duration,
    plural,
    safe_terminal_text,
    timestamp_from_epoch,
)

try:  # Optional dependency; the ANSI path is complete without it.
    from rich.console import Console as _RichConsole
    from rich.text import Text as _RichText

    _RICH_AVAILABLE = True
except Exception:  # noqa: BLE001  # pragma: no cover - optional Rich import
    _RichConsole = None  # type: ignore[assignment]
    _RichText = None  # type: ignore[assignment]
    _RICH_AVAILABLE = False


ANSI = {
    "reset": "0",
    "bold": "1",
    "dim": "2",
    "underline": "4",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "white": "37",
    "bright_cyan": "96",
    "bright_red": "91",
    "bright_green": "92",
    "bright_yellow": "93",
}
GROUP_COLOURS = {
    RiskGroup.SAFE: "green",
    RiskGroup.CAREFUL: "yellow",
    RiskGroup.REBUILD: "magenta",
}


class UserCancelled(Exception):
    """The person stopped a prompt instead of making a selection."""


class UI:
    def __init__(
        self,
        *,
        stream: TextIO | None = None,
        color: bool | None = None,
        width: int | None = None,
    ) -> None:
        self.stream = stream or sys.stdout
        self.is_tty = bool(getattr(self.stream, "isatty", lambda: False)())
        env_no_color = bool(os.environ.get("NO_COLOR"))
        if color is None:
            color = self.is_tty and not env_no_color
        self.color = bool(color)
        if width is None:
            width = shutil.get_terminal_size(fallback=(100, 24)).columns
        self.width = max(64, min(140, int(width)))
        self._rich = None
        if self.color and _RICH_AVAILABLE:
            try:
                self._rich = _RichConsole(
                    file=self.stream,
                    highlight=False,
                    emoji=False,
                    width=self.width,
                    soft_wrap=False,
                )
            except Exception:  # noqa: BLE001 - fall back to ANSI
                self._rich = None
        self._progress_active = False

    # ------------------------------------------------------------------
    # Low-level styled output
    # ------------------------------------------------------------------
    def _style(self, text: str, name: str) -> str:
        if not self.color:
            return text
        return f"\x1b[{ANSI[name]}m{text}\x1b[0m"

    def bold(self, text: object) -> str:
        return self._style(str(text), "bold")

    def dim(self, text: object) -> str:
        return self._style(str(text), "dim")

    def red(self, text: object) -> str:
        return self._style(str(text), "red")

    def green(self, text: object) -> str:
        return self._style(str(text), "green")

    def yellow(self, text: object) -> str:
        return self._style(str(text), "yellow")

    def cyan(self, text: object) -> str:
        return self._style(str(text), "cyan")

    def magenta(self, text: object) -> str:
        return self._style(str(text), "magenta")

    def accent(self, text: object) -> str:
        return self._style(str(text), "bright_cyan")

    def safe(self, value: object) -> str:
        return safe_terminal_text(value)

    def print(self, text: object = "", *, end: str = "\n", flush: bool = True) -> None:
        value = str(text)
        if self._rich is not None:
            try:
                self._rich.print(_RichText.from_ansi(value), end=end, highlight=False)
                if flush:
                    self.stream.flush()
                return
            except Exception:  # noqa: BLE001 - fall back to ANSI
                self._rich = None
        self.stream.write(value + end)
        if flush:
            self.stream.flush()

    def blank(self) -> None:
        self.print()

    def rule(self, title: str = "") -> None:
        title = self.safe(title)
        if self._rich is not None:
            try:
                self._rich.rule(title or "", style="cyan")
                return
            except Exception:  # noqa: BLE001 - fall back to ANSI
                self._rich = None
        if title:
            left = "── "
            right = " "
            available = max(10, self.width - len(left) - len(right) - len(title) - 1)
            self.print(self.cyan(left + title + right + "─" * available))
        else:
            self.print(self.cyan("─" * self.width))

    def section(self, title: str, subtitle: str = "") -> None:
        self.blank()
        self.print(self.bold(self.accent(title)))
        if subtitle:
            self.print(self.dim(subtitle))
        self.print(self.dim("─" * min(self.width, max(20, len(title) + 12))))

    def error(self, text: object) -> None:
        self.print(self.red("Error: ") + self.safe(text))

    def warning(self, text: object) -> None:
        self.print(self.yellow("Warning: ") + self.safe(text))

    def success(self, text: object) -> None:
        self.print(self.green("Done: ") + self.safe(text))

    def note(self, text: object) -> None:
        self.print(self.dim(self.safe(text)))

    # ------------------------------------------------------------------
    # Progress
    # ------------------------------------------------------------------
    def progress(self, line: str) -> None:
        if not self.is_tty:
            return
        if self._rich is not None:
            # A static line avoids mixing a live Rich display with prompts.
            self.stream.write("\r" + safe_terminal_text(line)[: self.width - 1])
            self.stream.write("\x1b[K")
            self.stream.flush()
            self._progress_active = True
            return
        visible = safe_terminal_text(line)
        if len(visible) > self.width - 1:
            visible = visible[: self.width - 2] + "…"
        self.stream.write("\r" + visible + "\x1b[K")
        self.stream.flush()
        self._progress_active = True

    def clear_progress(self) -> None:
        if self._progress_active and self.is_tty:
            self.stream.write("\r\x1b[2K")
            self.stream.flush()
        self._progress_active = False

    # ------------------------------------------------------------------
    # Report sections
    # ------------------------------------------------------------------
    def banner(self, app_name: str, version: str, tagline: str, mode: str) -> None:
        self.rule()
        self.print(self.bold(self.accent(f"{app_name} {version}")))
        self.print(self.dim(tagline))
        self.print(f"Scan mode: {self.bold(mode.upper())}")
        self.print(
            self.dim(
                "Read-only until you confirm a cleanup plan at the end. "
                "Nothing is deleted just by scanning."
            )
        )
        self.rule()

    def storage(self, scan: ScanResult) -> None:
        self.section("STORAGE", "Capacity comes from the live filesystem, not from file totals.")
        if not scan.filesystems:
            self.warning("No filesystem capacity information was available.")
        for filesystem in scan.filesystems:
            percent = max(0.0, min(100.0, filesystem.used_percent))
            filled = round((percent / 100.0) * 32)
            bar = "█" * filled + "░" * (32 - filled)
            bar_colour = "red" if percent >= 90 else "yellow" if percent >= 75 else "green"
            colour_fn = getattr(self, bar_colour)
            self.print(
                f"  {self.bold(self.safe(filesystem.path))} "
                f"{self.dim('(' + self.safe(filesystem.fstype) + ')')}"
            )
            self.print(f"  {colour_fn(bar)} {percent:5.1f}% used")
            self.print(
                f"  {human_bytes(filesystem.used)} used · "
                f"{human_bytes(filesystem.available)} free · "
                f"{human_bytes(filesystem.total)} total"
            )
            if filesystem.source:
                self.print(self.dim(f"  Source: {self.safe(filesystem.source)}"))

        self.blank()
        self.print(
            f"  Readable file data: {self.bold(human_bytes(scan.total_bytes))} "
            f"across {scan.unique_files:,} unique files · "
            f"{scan.total_dirs:,} folders · "
            f"{human_duration(scan.elapsed_seconds)}"
        )
        self.print(
            self.dim(
                "  This is what AppSweep could read. Shared files, compression, "
                "reflinks, and unreadable locations can make it differ from disk use."
            )
        )
        if scan.total_symlinks:
            self.print(self.dim(f"  Symbolic links seen (never followed): {scan.total_symlinks:,}"))
        if scan.permission_error_count:
            self.warning(
                f"{scan.permission_error_count} location(s) could not be fully read. "
                "The report does not pretend coverage is complete."
            )
            for issue in scan.permission_issues[:5]:
                self.print(
                    self.dim(
                        f"  {self.safe(issue.path)} · {self.safe(issue.reason)}"
                    )
                )
            if scan.permission_error_count > 5:
                self.print(self.dim(f"  … and {scan.permission_error_count - 5} more"))
        if scan.skipped_paths:
            self.print(
                self.dim(
                    f"  Deliberately skipped: {len(scan.skipped_paths)} mount/path(s)"
                )
            )
            for skipped in scan.skipped_paths[:5]:
                fstype = f" ({self.safe(skipped.fstype)})" if skipped.fstype else ""
                self.print(
                    self.dim(
                        f"  {self.safe(skipped.path)} · {self.safe(skipped.reason)}{fstype}"
                    )
                )
            if len(scan.skipped_paths) > 5:
                self.print(self.dim(f"  … and {len(scan.skipped_paths) - 5} more"))
        if scan.sandboxed:
            self.warning(
                "AppSweep is inside a Flatpak sandbox. Host-only paths and package "
                "tools may be unreachable; only accessible local data was analyzed."
            )

    def capabilities(self, report: CapabilityReport | None) -> None:
        """Show which optional host tools were found and what is unavailable."""

        if report is None or not report.tools:
            return
        self.section(
            "HOST INTEGRATIONS",
            "Missing optional tools disable only their own cleanup categories; "
            "scanning and unrelated cleanup still work.",
        )
        available = report.available_tools
        missing = report.missing_tools
        if available:
            names = ", ".join(self.safe(tool.name) for tool in available)
            self.print(f"  {self.green('Available')}: {names}")
        if missing:
            names = ", ".join(self.safe(tool.name) for tool in missing)
            self.print(f"  {self.yellow('Missing')}: {names}")
            for warning in report.warnings:
                wrapped = textwrap.wrap(
                    self.safe(warning), width=max(40, self.width - 6)
                )
                if not wrapped:
                    continue
                self.print(f"  {self.yellow('•')} {wrapped[0]}")
                for line in wrapped[1:]:
                    self.print(f"    {line}")
        else:
            self.print(self.green("  All detected host integrations are available."))
        self.print(self.dim(f"  {VERIFICATION_NOTE}"))

    def cleanup_report(self, report: CleanupReport, findings: Iterable[ReportOnlyFinding]) -> None:
        self.section(
            "CLEANUP OPPORTUNITIES",
            "Numbered items are separate from the personal-file list. Every number "
            "is a separate choice.",
        )
        groups = (
            (RiskGroup.SAFE, report.safe_items),
            (RiskGroup.CAREFUL, report.careful_items),
            (RiskGroup.REBUILD, report.rebuild_items),
        )
        for group, items in groups:
            self.blank()
            colour = getattr(self, GROUP_COLOURS[group])
            self.print(colour(self.bold(f"{group.label} — {group.explanation}")))
            if not items:
                self.print(self.dim("  No cleanup opportunities in this group."))
                continue
            for item in items:
                self.cleanup_item(item)
        findings = list(findings)
        if findings:
            self.blank()
            self.print(self.bold("Report-only findings"))
            self.print(
                self.dim(
                    "These explain disk use but are not selected or changed by AppSweep."
                )
            )
            for finding in findings:
                self.finding(finding)

    def cleanup_item(self, item: CleanupItem) -> None:
        status = ""
        if not item.eligible:
            status = self.red("  NOT SELECTABLE")
        estimate = self.dim("  estimated") if item.estimated else ""
        self.blank()
        self.print(
            f"  {self.accent(f'[{item.number:>2}]')} "
            f"{self.bold(self.safe(item.title))}  "
            f"{self.bold(human_bytes(item.size_bytes))}{estimate}{status}"
        )
        self.print(f"       {self.dim('What it is:')} {self.safe(item.description)}")
        if item.paths:
            locations = " · ".join(self.safe(path) for path in item.paths)
        else:
            locations = "Managed through the system tool shown below; no direct file path."
        self.print(f"       {self.dim('Where:')} {locations}")
        self.print(f"       {self.dim('Why it appears:')} {self.safe(item.summary)}")
        self.print(f"       {self.dim('What happens if cleaned:')} {self.safe(item.consequences)}")
        self.print(f"       {self.dim('How AppSweep cleans it:')} {self.safe(item.method)}")
        if item.estimated:
            self.print(
                self.dim(
                    "       Size is an estimate prepared from the current system; "
                    "the system tool makes the final decision."
                )
            )
        if item.details:
            shown = item.details[:10]
            self.print(f"       {self.dim('Details:')}")
            for detail in shown:
                self.print(self.dim(f"         · {self.safe(detail)}"))
            if len(item.details) > len(shown):
                self.print(self.dim(f"         · … and {len(item.details) - len(shown)} more"))
        if item.preview:
            preview_lines = item.preview.splitlines()
            self.print(f"       {self.dim('Preview:')}")
            for line in preview_lines[:6]:
                self.print(self.dim(f"         {self.safe(line)}"))
        if not item.eligible:
            self.warning(
                f"Item {item.number} is report-only: "
                f"{self.safe(item.report_only_reason or 'not eligible for automatic cleanup')}"
            )

    def finding(self, finding: ReportOnlyFinding) -> None:
        self.blank()
        self.print(
            f"  {self.bold(self.safe(finding.title))}  "
            f"{human_bytes(finding.size_bytes)}"
        )
        self.print(f"       {self.dim('What:')} {self.safe(finding.detail)}")
        self.print(f"       {self.dim('Why it is report-only:')} {self.safe(finding.reason)}")

    def files(self, files: Iterable[FileSnapshot]) -> None:
        files = list(files)
        self.section(
            "LARGEST PERSONAL FILES",
            "Only files owned by you, above the minimum size, outside caches, hidden "
            "configuration, Trash, and system data. AppSweep did not open them.",
        )
        if not files:
            self.print(
                "  No personal files matched the minimum size in the scanned folders."
            )
            return
        self.print(
            self.dim(
                f"  {'#':>3}  {'Size':>10}  {'Modified':<16}  {'Type':<10}  File"
            )
        )
        for index, file in enumerate(files, start=1):
            size = human_bytes(file.size)
            modified = timestamp_from_epoch(file.mtime)
            kind = file.type_label[:10]
            self.print(
                f"  {index:>3}  {size:>10}  {modified:<16}  "
                f"{self.safe(kind):<10}  {self.bold(self.safe(file.filename))}"
            )
            self.print(f"       {self.dim('Path:')} {self.safe(file.path)}")
            self.print(
                f"       {self.dim('Bytes:')} {file.size:,}  "
                f"{self.dim('·')} {self.safe(file.description)}"
            )
        self.print()
        self.print(
            self.yellow(
                "  Moving a file to Trash does not free disk space until Trash is "
                "emptied."
            )
        )

    def notes(self, notes: Iterable[str]) -> None:
        notes = [note for note in notes if note]
        if not notes:
            return
        self.section("NOTES", "Things to know before choosing a cleanup.")
        for note in notes:
            wrapped = textwrap.wrap(self.safe(note), width=max(40, self.width - 6))
            if not wrapped:
                continue
            self.print(f"  • {wrapped[0]}")
            for line in wrapped[1:]:
                self.print(f"    {line}")

    # ------------------------------------------------------------------
    # Selection and confirmation
    # ------------------------------------------------------------------
    def selected_files(self, files: Iterable[FileSnapshot], permanent: bool) -> None:
        files = list(files)
        if not files:
            return
        verb = (
            "move to Trash (permanent deletion is disabled)"
            if permanent
            else "move to Trash"
        )
        self.blank()
        self.print(self.bold(f"Selected personal files — {verb}:"))
        total = 0
        for file in files:
            total += file.size
            self.print(
                f"  · {self.safe(file.path)}  {self.dim(human_bytes(file.size))}"
            )
        self.print(self.dim(f"  Total: {human_bytes(total)}"))

    def selected_items(self, items: Iterable[CleanupItem]) -> None:
        items = list(items)
        if not items:
            return
        self.blank()
        self.print(self.bold("Selected cleanup items:"))
        total = sum(item.size_bytes for item in items)
        estimated = sum(item.size_bytes for item in items if item.estimated)
        for item in items:
            marker = " (estimate)" if item.estimated else ""
            self.print(
                f"  · [{item.number}] {self.safe(item.title)}  "
                f"{self.dim(human_bytes(item.size_bytes) + marker)}"
            )
        summary = f"  Total shown: {human_bytes(total)}"
        if estimated:
            summary += f" · {human_bytes(estimated)} estimated by a system tool"
        self.print(self.dim(summary))

    def confirmation_summary(
        self,
        files: Iterable[FileSnapshot],
        items: Iterable[CleanupItem],
        permanent: bool,
    ) -> None:
        files = list(files)
        items = list(items)
        self.section("READY TO CLEAN", "Nothing has changed yet.")
        file_total = sum(file.size for file in files)
        item_total = sum(item.size_bytes for item in items)
        if files:
            verb = (
                "moved to Trash (permanent deletion is disabled)"
                if permanent
                else "moved to Trash"
            )
            self.print(
                f"  Personal files: {len(files)} · {human_bytes(file_total)} "
                f"→ {verb}"
            )
        if items:
            estimated = sum(item.size_bytes for item in items if item.estimated)
            estimate_text = (
                f" ({human_bytes(estimated)} estimated by a system tool)"
                if estimated
                else ""
            )
            self.print(
                f"  Cleanup items: {len(items)} · {human_bytes(item_total)} shown"
                f"{estimate_text}"
            )
        admin_items = [item for item in items if item.needs_admin]
        if admin_items:
            self.print(
                self.yellow(
                    f"  Administrator approval will be requested separately for "
                    f"{len(admin_items)} item(s), one command at a time."
                )
            )
        if permanent:
            self.print(
                self.red(
                    "  Permanent deletion is disabled; this plan will be refused."
                )
            )
        if any(item.action.value == "empty_trash" for item in items) and files and not permanent:
            self.print(
                self.yellow(
                    "  Trash is emptied before selected files are moved, so the newly "
                    "moved files will remain in Trash afterwards."
                )
            )
        self.print(
            self.dim(
                "  Cleanup commands run one at a time. A failure is reported without "
                "continuing to later items in the same command group."
            )
        )

    def ask(self, prompt: str, *, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        self.print(self.cyan(prompt + suffix + " "), end="", flush=True)
        try:
            answer = sys.stdin.readline()
        except KeyboardInterrupt:
            self.print()
            raise UserCancelled("Cancelled by the user.") from None
        if answer == "":
            self.print()
            return ""
        return answer.rstrip("\n")

    def ask_yes_no(self, prompt: str, *, default: bool = False) -> bool:
        default_text = "Y/n" if default else "y/N"
        while True:
            answer = self.ask(prompt + f" [{default_text}]").strip().lower()
            if answer == "":
                return default
            if answer in {"y", "yes"}:
                return True
            if answer in {"n", "no"}:
                return False
            self.warning("Please answer yes or no.")

    def ask_exact(self, prompt: str, phrase: str) -> bool:
        answer = self.ask(prompt).strip()
        return answer.upper() == phrase.upper()


def supports_rich() -> bool:
    return _RICH_AVAILABLE


def mode_label(mode: str) -> str:
    return {
        "quick": "QUICK",
        "full": "FULL",
        "custom": "CUSTOM",
    }.get(mode.lower(), mode.upper())


def describe_file_action(files: list[FileSnapshot], permanent: bool) -> str:
    if not files:
        return "no personal files"
    verb = "delete permanently" if permanent else "move to Trash"
    return f"{len(files)} {plural(len(files), 'file')} to {verb} ({human_bytes(sum(f.size for f in files))})"


def describe_cleanup_action(items: list[CleanupItem]) -> str:
    if not items:
        return "no cleanup items"
    return f"{len(items)} cleanup {plural(len(items), 'item')} ({human_bytes(sum(i.size_bytes for i in items))} shown)"


def path_basename(path: str) -> str:
    try:
        return Path(path).name or path
    except (OSError, ValueError):
        return path
