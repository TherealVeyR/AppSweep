"""Command-line workflow for AppSweep.

The application is deliberately run as the logged-in user. It refuses to run
as root. Individual system commands ask for administrator approval at the
moment they are needed through pkexec, never by elevating the whole program.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import APP_NAME, TAGLINE, __version__
from .analyzer import Analysis, all_report_only, build_report
from .audit import append_cleanup_event, audit_log_path
from .capabilities import detect_capabilities
from .categories import (
    CATEGORY_CHOICES,
    CleanupDiscovery,
    discover_cleanup,
)
from .cleanup_engine import CleanupEngine
from .filesystem import discover_installation_roots
from .large_files import (
    discover_application_roots,
    personal_excluded_paths,
    quick_personal_roots,
)
from .models import (
    CleanupItem,
    CleanupPlan,
    FileSnapshot,
    ScanConfig,
    ScanResult,
)
from .reporting import ExportError, render_csv, render_json, write_export
from .runner import CommandRunner
from .safety import SafetyPolicy, make_authorization
from .scanner import StorageScanner, format_progress_line
from .selection import (
    ParsedSelection,
    SelectionError,
    parse_cleanup_selection,
    parse_file_selection,
)
from .ui import UI, UserCancelled, mode_label
from .util import human_bytes, now_iso, parse_size, safe_terminal_text


class UsageError(ValueError):
    """The command line cannot be turned into a safe scan."""


@dataclass
class ScanSetup:
    config: ScanConfig
    notes: list[str]
    mode: str

    @property
    def roots(self) -> tuple[str, ...]:
        return self.config.roots


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="appssweep",
        description=(
            "See what is using your Fedora disk and choose safe cleanup. "
            "Report-only by default; nothing is deleted without explicit confirmation."
        ),
        epilog=(
            "Group meanings: SAFE = caches and temporary by-products; "
            "CAREFUL = your files or Trash; REBUILD = package data that can be "
            "downloaded or installed again. Full scan can take a while; press "
            "Ctrl+C to stop and keep the partial report."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=("quick", "full", "custom"),
        default="quick",
        help="quick = common personal folders; full = live installation; custom = --path",
    )
    parser.add_argument(
        "--path",
        action="append",
        dest="paths",
        default=[],
        metavar="PATH",
        help="custom scan root; repeat for more than one (requires --mode custom)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        dest="excludes",
        default=[],
        metavar="PATH",
        help="additional path to leave out of the scan; repeatable",
    )
    parser.add_argument(
        "--min-size",
        default="1M",
        metavar="SIZE",
        help="smallest personal file to rank (default: 1M; use 0 for all files)",
    )
    parser.add_argument(
        "--categories",
        default="all",
        metavar="LIST",
        help=(
            "cleanup categories: all, none, or a comma-separated subset of "
            + ", ".join(CATEGORY_CHOICES)
        ),
    )
    parser.add_argument(
        "--report-only",
        "--dry-run",
        dest="report_only",
        action="store_true",
        help="show the report and do not offer any cleanup (dry-run alias)",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="show cleanup prompts even if standard input is not a terminal",
    )
    parser.add_argument(
        "--refresh-metadata",
        action="store_true",
        help="allow a DNF autoremove preview to refresh repository metadata",
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        metavar="FILE",
        help="write the complete report as JSON",
    )
    parser.add_argument(
        "--csv",
        dest="csv_path",
        metavar="FILE",
        help="write the report as a single CSV with record_type rows",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="disable terminal colours",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def ensure_supported_environment(euid: int | None = None) -> str:
    """Return a refusal message when the environment is unsafe."""

    if not sys.platform.startswith("linux"):
        return "AppSweep currently supports Linux (Fedora) only."
    if (os.geteuid() if euid is None else euid) == 0:
        return (
            "Do not run AppSweep as root. Run it as your normal desktop user. "
            "Individual system commands request administrator approval with "
            "pkexec only when needed."
        )
    return ""


def parse_categories(raw: str) -> tuple[str, ...]:
    text = (raw or "").strip().lower()
    if text in {"", "all"}:
        return CATEGORY_CHOICES
    if text in {"none", "off", "no"}:
        return ()
    requested = {part.strip() for part in text.split(",") if part.strip()}
    unknown = requested - set(CATEGORY_CHOICES)
    if unknown:
        raise UsageError(
            "Unknown cleanup category: "
            + ", ".join(sorted(unknown))
            + ". Choose from "
            + ", ".join(CATEGORY_CHOICES)
            + ", all, or none."
        )
    return tuple(category for category in CATEGORY_CHOICES if category in requested)


def build_scan_setup(
    args: argparse.Namespace,
    *,
    home: str | None = None,
) -> ScanSetup:
    home_path = os.path.normpath(
        os.path.abspath(os.path.expanduser(home or str(Path.home())))
    )
    if not os.path.isdir(home_path):
        raise UsageError(f"Your home folder could not be found: {home_path}")

    mode = str(args.mode)
    notes: list[str] = []
    custom_paths = [str(path) for path in (getattr(args, "paths", None) or [])]

    if mode != "custom" and custom_paths:
        raise UsageError("--path can only be used with --mode custom.")
    if mode == "custom" and not custom_paths:
        raise UsageError("--mode custom needs at least one --path. Example: --path ~/Downloads")

    if mode == "quick":
        roots = quick_personal_roots(home_path)
        if roots:
            notes.append(
                "Quick scan covers common personal folders only: "
                + ", ".join(os.path.basename(root) for root in roots)
                + ". Use --mode full for the whole installation or --path for other folders."
            )
        else:
            roots = (home_path,)
            notes.append(
                "No common personal folders were found, so the quick scan covers "
                "your home folder without following symbolic links."
            )
        personal_roots = tuple(roots)
    elif mode == "full":
        roots, root_notes, _mounts = discover_installation_roots(home_path)
        notes.extend(root_notes)
        notes.append(
            "Full scan follows the live installation, keeps separate local filesystems, "
            "and skips virtual, network, and external mounts."
        )
        personal_roots = (home_path,)
    else:
        roots = []
        for raw in custom_paths:
            if not str(raw).strip():
                raise UsageError("Custom scan paths cannot be empty.")
            try:
                path = os.path.normpath(os.path.abspath(os.path.expanduser(raw)))
                exists = os.path.lexists(path)
            except (OSError, ValueError, RuntimeError) as exc:
                raise UsageError(
                    f"Custom scan path is not valid: {safe_terminal_text(raw)}"
                ) from exc
            if not exists:
                raise UsageError(
                    f"Custom scan path does not exist: {safe_terminal_text(raw)}"
                )
            roots.append(path)
            if os.path.islink(path):
                notes.append(
                    f"Custom path {path} is a symbolic link; AppSweep reports it "
                    "but never follows it."
                )
        roots = tuple(dict.fromkeys(roots))
        personal_roots = roots
        notes.append(
            "Custom scan covers only the path(s) you supplied: "
            + ", ".join(roots)
            + "."
        )

    if not roots:
        raise UsageError("No readable scan roots were available.")

    try:
        min_size = parse_size(str(getattr(args, "min_size", "1M")))
    except ValueError as exc:
        raise UsageError(f"Invalid --min-size value: {exc}") from exc

    config = ScanConfig(
        mode=mode,
        roots=tuple(roots),
        min_file_size=min_size,
        top_n=50,
        top_dirs=20,
        excluded_paths=tuple(getattr(args, "excludes", None) or ()),
        personal_roots=tuple(personal_roots),
        personal_excluded_paths=personal_excluded_paths(home_path, roots),
        application_roots=discover_application_roots(home_path),
        follow_symlinks=False,
        detect_mounts=True,
        include_system_files=True,
    ).normalized()
    if not config.roots:
        raise UsageError(
            "No readable scan roots were available after checking the supplied paths."
        )
    return ScanSetup(config=config, notes=notes, mode=mode)


def run_scan(setup: ScanSetup, ui: UI) -> tuple[ScanResult, str]:
    started = now_iso()
    ui.print("Scanning your files read-only. Press Ctrl+C to stop at any time.")
    if ui.is_tty:
        ui.print(ui.dim("Progress is shown below; the partial report is kept if you stop."))
    scanner = StorageScanner(
        setup.config,
        progress=lambda current, files, directories, size, elapsed: ui.progress(
            format_progress_line(current, files, directories, size, elapsed)
        ),
    )
    result = scanner.scan()
    ui.clear_progress()
    if result.cancelled:
        ui.warning("Scan cancelled. Coverage is incomplete and cleanup is disabled.")
    else:
        ui.success(
            f"Scan finished in {result.elapsed_seconds:.1f}s. "
            "No changes have been made."
        )
    return result, started


def run_discovery(
    scan: ScanResult,
    categories: Sequence[str],
    *,
    runner: CommandRunner,
    home: str,
    allow_metadata_refresh: bool,
    ui: UI,
) -> CleanupDiscovery:
    if not categories:
        return CleanupDiscovery(capabilities=detect_capabilities(runner))
    ui.print("Looking for cleanup opportunities (read-only discovery)…")
    try:
        return discover_cleanup(
            scan,
            categories=categories,
            runner=runner,
            allow_metadata_refresh=allow_metadata_refresh,
            home=home,
        )
    except Exception as exc:  # keep a usable report if discovery fails  # noqa: BLE001
        ui.warning(f"Cleanup discovery was incomplete: {exc}")
        return CleanupDiscovery(
            notes=[f"Cleanup discovery failed: {exc}"],
            capabilities=detect_capabilities(runner),
        )


def export_reports(
    args: argparse.Namespace,
    report,
    analysis: Analysis,
    discovery: CleanupDiscovery,
    ui: UI,
) -> bool:
    failed = False
    exports = (
        ("JSON", getattr(args, "json_path", None), lambda: render_json(report, analysis, discovery)),
        ("CSV", getattr(args, "csv_path", None), lambda: render_csv(report, analysis, discovery)),
    )
    for label, path, renderer in exports:
        if not path:
            continue
        if str(path) == "-":
            ui.error(f"{label} export needs a file path, not '-'.")
            failed = True
            continue
        try:
            write_export(str(path), renderer())
        except ExportError as exc:
            ui.error(f"Could not write the {label} report: {exc}")
            failed = True
        else:
            ui.success(f"{label} report written to {ui.safe(os.path.abspath(os.path.expanduser(str(path))))}")
    return failed


def wait_for_selection(
    ui: UI,
    prompt: str,
    parser,
    choices,
    *,
    on_cancel: str,
) -> ParsedSelection:
    while True:
        answer = ui.ask(prompt)
        try:
            parsed = parser(answer, choices)
        except SelectionError as exc:
            ui.error(exc)
            ui.print(
                ui.dim(
                    "Use numbers, ranges (1-5), commas, all, none, or cancel. "
                    "An item in CAPS/NOT SELECTABLE cannot be chosen."
                )
            )
            continue
        if parsed.cancelled:
            ui.print(on_cancel)
        return parsed


def interactive_cleanup(
    report,
    discovery: CleanupDiscovery,
    *,
    runner: CommandRunner,
    home: str,
    ui: UI,
) -> int:
    files = list(report.personal_files)
    items = list(report.cleanup_items)
    eligible_items = [item for item in items if item.eligible]

    if not files and not eligible_items:
        ui.print("There are no selectable personal files or cleanup items in this report.")
        ui.print(ui.dim("No changes were made."))
        return 0

    selected_files: list[FileSnapshot] = []
    if files:
        ui.print()
        ui.print(
            ui.bold(
                "Choose personal files now. They are moved to Trash by default; "
                "permanent deletion is a separate, extra-confirmed choice."
            )
        )
        file_map = {index: file for index, file in enumerate(files, start=1)}
        parsed = wait_for_selection(
            ui,
            "Files to select (examples: 1, 1,3,5, 1-10, all, none, cancel):",
            parse_file_selection,
            file_map,
            on_cancel="File selection cancelled. No changes will be made.",
        )
        if parsed.cancelled:
            return 130
        selected_files = parsed.resolve(file_map)
        if not selected_files:
            ui.print("No personal files selected.")

    permanent = False
    if selected_files:
        ui.print()
        if ui.ask_yes_no(
            "Permanently delete these files instead of moving them to Trash?",
            default=False,
        ):
            ui.warning(
                "Permanent deletion cannot be undone. The files are unlinked directly, "
                "not moved to Trash."
            )
            if ui.ask_exact(
                "Type DELETE PERMANENTLY to confirm the irreversible action:",
                "DELETE PERMANENTLY",
            ):
                permanent = True
            else:
                ui.warning(
                    "Permanent deletion was not confirmed. Those files will be moved "
                    "to Trash instead."
                )

    selected_items: list[CleanupItem] = []
    if eligible_items:
        ui.print()
        ui.print(
            ui.bold(
                "Choose cleanup items. They are separate from the personal files above."
            )
        )
        item_map = {item.number: item for item in items}
        parsed = wait_for_selection(
            ui,
            "Cleanup items to select (numbers, ranges, safe/careful/rebuild, all, none, cancel):",
            parse_cleanup_selection,
            item_map,
            on_cancel="Cleanup selection cancelled. No changes will be made.",
        )
        if parsed.cancelled:
            return 130
        selected_items = parsed.resolve(item_map)
        if selected_items:
            ui.selected_items(selected_items)
        else:
            ui.print("No cleanup items selected.")

    if not selected_files and not selected_items:
        ui.print()
        ui.print("Nothing selected. No changes were made.")
        return 0

    ui.selected_files(selected_files, permanent)
    ui.selected_items(selected_items)
    ui.confirmation_summary(selected_files, selected_items, permanent)
    if not ui.ask_exact(
        "Type CLEAN to carry out this exact plan, or press Enter to cancel:",
        "CLEAN",
    ):
        ui.print("Cancelled. No changes were made.")
        return 0

    plan = CleanupPlan(
        cleanup_items=selected_items,
        personal_files=selected_files,
        permanent_personal=permanent,
    )
    authorization = make_authorization(
        selected_items,
        selected_files,
        permanent_personal=permanent,
    )
    trash_module = discovery.modules.get("trash")
    trash_root = getattr(trash_module, "trash_root", "") if trash_module else ""
    policy = SafetyPolicy(
        home=home,
        personal_roots=report.scan.config.personal_roots,
        cache_roots=discovery.cache_roots,
        trash_roots=(trash_root,) if trash_root else (),
    )
    filesystem_path = (
        report.scan.filesystem.path if report.scan.filesystem else home
    )
    engine = CleanupEngine(policy, runner, filesystem_path=filesystem_path)
    audit_path = audit_log_path(home)
    audit_error = append_cleanup_event(
        "cleanup_started",
        plan,
        authorization,
        home=home,
        log_path=audit_path,
    )
    ui.section("CLEANING", "The plan below was confirmed by typing CLEAN.")
    try:
        execution = engine.execute_plan(plan, authorization)
    except (PermissionError, ValueError) as exc:
        error = str(exc)
        audit_refusal_error = append_cleanup_event(
            "cleanup_refused",
            plan,
            authorization,
            home=home,
            error=error,
            log_path=audit_path,
        )
        audit_error = audit_error or audit_refusal_error
        ui.error(error)
        ui.print(ui.dim("No further actions were attempted."))
        if audit_error:
            ui.warning(f"Cleanup audit log was not written: {audit_error}")
        return 1
    except Exception as exc:  # noqa: BLE001 - final cleanup safety boundary
        error = f"Unexpected cleanup failure: {safe_terminal_text(exc)}"
        audit_refusal_error = append_cleanup_event(
            "cleanup_refused",
            plan,
            authorization,
            home=home,
            error=error,
            log_path=audit_path,
        )
        audit_error = audit_error or audit_refusal_error
        ui.error(error)
        ui.print(ui.dim("No further actions were attempted."))
        if audit_error:
            ui.warning(f"Cleanup audit log was not written: {audit_error}")
        return 1

    finish_error = append_cleanup_event(
        "cleanup_finished",
        plan,
        authorization,
        home=home,
        execution=execution,
        log_path=audit_path,
    )
    audit_error = audit_error or finish_error
    if audit_error:
        ui.warning(f"Cleanup audit log was not written: {audit_error}")
    else:
        ui.print(ui.dim(f"Cleanup audit written to {ui.safe(audit_path)}"))

    print_execution_report(execution, ui)
    return 0 if execution.failed == 0 else 1


def print_execution_report(execution, ui: UI) -> None:
    ui.section("CLEANUP RESULTS", "Reported reclaim and actual free space are both shown.")
    if not execution.results:
        ui.print("No operations were executed.")
        return
    for result in execution.results:
        if result.skipped:
            status = ui.yellow("SKIPPED")
        elif result.success:
            status = ui.green("OK")
        else:
            status = ui.red("FAILED")
        ui.print(f"  {status}  {ui.bold(ui.safe(result.title))}")
        if result.message:
            for line in str(result.message).splitlines()[:8]:
                ui.print(f"       {ui.safe(line)}")
        if result.reclaimed_bytes:
            ui.print(ui.dim(f"       Reported reclaim: {human_bytes(result.reclaimed_bytes)}"))
        if result.command and not result.success:
            ui.print(ui.dim(f"       Command: {ui.safe(result.command)}"))

    ui.blank()
    if execution.filesystem_path:
        ui.print(
            f"  Filesystem free on {ui.safe(execution.filesystem_path)}: "
            f"before {human_bytes(execution.available_before)} · "
            f"after {human_bytes(execution.available_after)}"
        )
    else:
        ui.print(
            f"  Filesystem free before: {human_bytes(execution.available_before)} · "
            f"after: {human_bytes(execution.available_after)}"
        )
    if execution.filesystem_freed:
        ui.success(
            f"Actual free space increased by about {human_bytes(execution.filesystem_freed)}."
        )
    else:
        ui.print(ui.dim("  No measurable free-space increase yet."))
    ui.print(
        ui.dim(
            "  Reported reclaim is the size of removed data. The filesystem figure "
            "is the authoritative disk-space change."
        )
    )
    ui.print(
        ui.yellow(
            "  Files moved to Trash still occupy disk space until Trash is emptied."
        )
    )
    if execution.failed:
        ui.warning(
            f"{execution.failed} operation(s) failed. Review the messages above; "
            "nothing was retried automatically."
        )


def _run(args: argparse.Namespace) -> int:
    refusal = ensure_supported_environment()
    if refusal:
        print(f"Error: {safe_terminal_text(refusal)}", file=sys.stderr)
        return 2

    try:
        categories = parse_categories(args.categories)
    except UsageError as exc:
        print(f"Error: {safe_terminal_text(exc)}", file=sys.stderr)
        return 2

    home = os.path.normpath(os.path.abspath(os.path.expanduser(str(Path.home()))))
    try:
        setup = build_scan_setup(args, home=home)
    except UsageError as exc:
        print(f"Error: {safe_terminal_text(exc)}", file=sys.stderr)
        return 2

    ui = UI(color=False if getattr(args, "no_color", False) else None)
    ui.banner(APP_NAME, __version__, TAGLINE, mode_label(setup.mode))
    runner = CommandRunner()
    try:
        scan, scan_started = run_scan(setup, ui)
    except KeyboardInterrupt:
        ui.clear_progress()
        print("Scan cancelled. No changes were made.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - top-level scan error boundary
        ui.clear_progress()
        print(f"Error: the scan could not finish: {safe_terminal_text(exc)}", file=sys.stderr)
        return 1

    discovery = run_discovery(
        scan,
        categories,
        runner=runner,
        home=home,
        allow_metadata_refresh=bool(getattr(args, "refresh_metadata", False)),
        ui=ui,
    )
    analysis = build_report(
        scan,
        discovery,
        scan_started=scan_started,
        app_version=__version__,
        extra_notes=setup.notes,
    )
    report = analysis.report
    findings = all_report_only(discovery, analysis)

    ui.storage(scan)
    ui.capabilities(discovery.capabilities)
    ui.cleanup_report(report, findings)
    ui.files(report.personal_files)
    ui.notes(report.notes)

    export_failed = export_reports(args, report, analysis, discovery, ui)

    if scan.cancelled:
        ui.print()
        ui.print(
            ui.yellow(
                "The scan was cancelled, so no cleanup choices are offered. "
                "The partial report above is still useful for inspection."
            )
        )
        return 130 if not export_failed else 1

    interactive = bool(getattr(args, "interactive", False)) or (
        sys.stdin.isatty() and sys.stdout.isatty()
    )
    if getattr(args, "report_only", False) or not interactive:
        ui.print()
        ui.print(ui.bold("Report only. No changes were made."))
        if not getattr(args, "report_only", False) and not interactive:
            ui.print(
                ui.dim(
                    "Standard input is not a terminal, so cleanup prompts are disabled. "
                    "Use --interactive to choose cleanup explicitly."
                )
            )
        return 1 if export_failed else 0

    try:
        code = interactive_cleanup(
            report,
            discovery,
            runner=runner,
            home=home,
            ui=ui,
        )
    except UserCancelled as exc:
        ui.print(str(exc))
        ui.print("No changes were made.")
        return 130
    except KeyboardInterrupt:
        ui.print()
        ui.print("Cancelled. No changes were made.")
        return 130
    return 1 if export_failed else code


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return _run(args)
    except UserCancelled as exc:
        print(str(exc), file=sys.stderr)
        print("No changes were made.", file=sys.stderr)
        return 130
    except KeyboardInterrupt:
        print("Cancelled. No changes were made.", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
