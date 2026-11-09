"""CLI configuration, refusal, report-only, and interactive cleanup tests."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from appssweep.categories import CleanupDiscovery
from appssweep.cli import (
    UsageError,
    build_parser,
    build_scan_setup,
    ensure_supported_environment,
    main,
    parse_categories,
)
from appssweep.large_files import quick_personal_roots


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        (self.home / "Downloads").mkdir()
        (self.home / "Documents").mkdir()
        self.scan_root = Path(self.tmp.name) / "scan"
        self.scan_root.mkdir()
        self.env = patch.dict(
            os.environ,
            {
                "HOME": str(self.home),
                "XDG_CACHE_HOME": str(self.home / ".cache"),
                "XDG_DATA_HOME": str(self.home / ".local" / "share"),
                "XDG_STATE_HOME": str(self.home / ".local" / "state"),
                "NO_COLOR": "1",
            },
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def args(self, **overrides) -> Namespace:
        values = {
            "mode": "custom",
            "paths": [str(self.scan_root)],
            "excludes": [],
            "min_size": "1M",
            "categories": "none",
            "report_only": True,
            "interactive": False,
            "refresh_metadata": False,
            "json_path": None,
            "csv_path": None,
            "no_color": True,
        }
        values.update(overrides)
        return Namespace(**values)

    def test_ensure_supported_environment_refuses_root(self) -> None:
        self.assertTrue(ensure_supported_environment(euid=0))
        self.assertEqual(ensure_supported_environment(euid=1000), "")

    def test_parse_categories(self) -> None:
        self.assertEqual(
            parse_categories("cache, logs"),
            ("cache", "logs"),
        )
        self.assertEqual(parse_categories("all"), ("packages", "flatpak", "cache", "logs", "trash"))
        self.assertEqual(parse_categories("none"), ())
        with self.assertRaises(UsageError):
            parse_categories("cache,games")

    def test_dry_run_alias_sets_report_only(self) -> None:
        args = build_parser().parse_args(["--dry-run"])
        self.assertTrue(args.report_only)

    def test_custom_scan_setup_requires_existing_paths(self) -> None:
        with self.assertRaises(UsageError):
            build_scan_setup(self.args(paths=[]))
        with self.assertRaises(UsageError):
            build_scan_setup(self.args(paths=[str(self.scan_root / "missing")]))

    def test_custom_scan_setup_rejects_empty_or_invalid_paths(self) -> None:
        with self.assertRaises(UsageError):
            build_scan_setup(self.args(paths=["   "]))
        with self.assertRaises(UsageError):
            build_scan_setup(self.args(paths=["\x00"]))

    def test_scan_config_normalization_drops_empty_roots(self) -> None:
        from appssweep.models import ScanConfig

        self.assertEqual(ScanConfig(roots=("",)).normalized().roots, ())
        self.assertEqual(
            ScanConfig(roots=("", str(self.scan_root))).normalized().roots,
            (str(self.scan_root),),
        )

    def test_quick_scan_setup_uses_common_folders(self) -> None:
        setup = build_scan_setup(self.args(mode="quick", paths=[]), home=str(self.home))
        self.assertEqual(setup.mode, "quick")
        self.assertEqual(
            set(setup.config.roots),
            set(quick_personal_roots(str(self.home))),
        )
        self.assertTrue(any("Quick scan" in note for note in setup.notes))

    def test_full_scan_setup_has_roots(self) -> None:
        setup = build_scan_setup(self.args(mode="full", paths=[]), home=str(self.home))
        self.assertEqual(setup.mode, "full")
        self.assertTrue(setup.config.roots)

    def test_refresh_metadata_flag_reaches_discovery(self) -> None:
        for extra_args, expected in (([], False), (["--refresh-metadata"], True)):
            with self.subTest(extra_args=extra_args):
                args = [
                    "--mode",
                    "custom",
                    "--path",
                    str(self.scan_root),
                    "--min-size",
                    "1M",
                    "--categories",
                    "cache",
                    "--report-only",
                    "--no-color",
                    *extra_args,
                ]
                stdout = io.StringIO()
                with (
                    patch(
                        "appssweep.cli.discover_cleanup",
                        return_value=CleanupDiscovery(),
                    ) as discovery,
                    contextlib.redirect_stdout(stdout),
                ):
                    code = main(args)
                self.assertEqual(code, 0, stdout.getvalue())
                discovery.assert_called_once()
                self.assertIs(
                    discovery.call_args.kwargs["allow_metadata_refresh"],
                    expected,
                )

    def test_report_only_main_writes_json_without_prompts(self) -> None:
        file = self.scan_root / "large.bin"
        file.write_bytes(b"x" * (2 * 1024 * 1024))
        report_path = Path(self.tmp.name) / "report.json"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(
                [
                    "--mode",
                    "custom",
                    "--path",
                    str(self.scan_root),
                    "--min-size",
                    "1M",
                    "--categories",
                    "none",
                    "--report-only",
                    "--no-color",
                    "--json",
                    str(report_path),
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["personal_files"][0]["path"], str(file))
        self.assertIn("Report only", stdout.getvalue())

    def test_invalid_path_returns_usage_error(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = main(
                [
                    "--mode",
                    "custom",
                    "--path",
                    str(self.scan_root / "missing"),
                    "--report-only",
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("does not exist", stderr.getvalue())

    def test_cli_error_escapes_control_characters(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = main(
                [
                    "--mode",
                    "custom",
                    "--path",
                    "\x1b[31mnot-a-real-path\x1b[0m",
                    "--report-only",
                ]
            )
        self.assertEqual(code, 2)
        output = stderr.getvalue()
        self.assertNotIn("\x1b", output)
        self.assertIn("\\x1b", output)

    def test_redirected_stdout_stays_report_only_even_with_tty_stdin(self) -> None:
        class FakeTTY(io.StringIO):
            def isatty(self) -> bool:
                return True

        stdout = io.StringIO()
        with (
            patch.object(sys, "stdin", FakeTTY("all\nCLEAN\n")),
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "--mode",
                    "custom",
                    "--path",
                    str(self.scan_root),
                    "--min-size",
                    "1M",
                    "--categories",
                    "none",
                    "--no-color",
                ]
            )
        self.assertEqual(code, 0)
        self.assertIn("Report only", stdout.getvalue())
        self.assertNotIn("Type CLEAN", stdout.getvalue())

    def test_interactive_cache_cleanup_confirms_before_execution(self) -> None:
        # No personal files; only a test cache. The input selects item 1 and
        # then types the final CLEAN phrase.
        cache_app = self.home / ".cache" / "TestApp"
        cache_app.mkdir(parents=True)
        cache_file = cache_app / "cache.bin"
        cache_file.write_bytes(b"c" * (2 * 1024 * 1024))

        old_stdin = sys.stdin
        sys.stdin = io.StringIO("1\nCLEAN\n")
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "--mode",
                        "custom",
                        "--path",
                        str(self.scan_root),
                        "--min-size",
                        "1M",
                        "--categories",
                        "cache",
                        "--interactive",
                        "--no-color",
                    ]
                )
        finally:
            sys.stdin = old_stdin
        output = stdout.getvalue()
        self.assertEqual(code, 0, output)
        self.assertFalse(cache_file.exists(), output)
        self.assertIn("READY TO CLEAN", output)
        self.assertIn("CLEANUP RESULTS", output)
        audit_log = self.home / ".local" / "state" / "appssweep" / "cleanup-audit.log"
        self.assertTrue(audit_log.is_file())
        audit_text = audit_log.read_text(encoding="utf-8")
        self.assertIn('"cleanup_started"', audit_text)
        self.assertIn('"cleanup_finished"', audit_text)

    def test_interactive_cleanup_warns_but_completes_if_audit_log_is_unsafe(
        self,
    ) -> None:
        cache_app = self.home / ".cache" / "TestApp"
        cache_app.mkdir(parents=True)
        cache_file = cache_app / "cache.bin"
        cache_file.write_bytes(b"c" * (2 * 1024 * 1024))
        unsafe_state = self.home / ".local" / "state" / "appssweep"
        unsafe_state.mkdir(parents=True)
        unsafe_state.chmod(0o777)

        old_stdin = sys.stdin
        sys.stdin = io.StringIO("1\nCLEAN\n")
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "--mode",
                        "custom",
                        "--path",
                        str(self.scan_root),
                        "--min-size",
                        "1M",
                        "--categories",
                        "cache",
                        "--interactive",
                        "--no-color",
                    ]
                )
        finally:
            sys.stdin = old_stdin
        output = stdout.getvalue()
        self.assertEqual(code, 0, output)
        self.assertFalse(cache_file.exists(), output)
        self.assertIn("Cleanup audit log was not written", output)
        self.assertFalse((unsafe_state / "cleanup-audit.log").exists())

    def test_interactive_cancel_makes_no_changes(self) -> None:
        cache_app = self.home / ".cache" / "TestApp"
        cache_app.mkdir(parents=True)
        cache_file = cache_app / "cache.bin"
        cache_file.write_bytes(b"c" * (2 * 1024 * 1024))

        old_stdin = sys.stdin
        sys.stdin = io.StringIO("1\n\n")
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "--mode",
                        "custom",
                        "--path",
                        str(self.scan_root),
                        "--min-size",
                        "1M",
                        "--categories",
                        "cache",
                        "--interactive",
                        "--no-color",
                    ]
                )
        finally:
            sys.stdin = old_stdin
        self.assertEqual(code, 0)
        self.assertTrue(cache_file.exists())
        self.assertIn("Cancelled. No changes were made.", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
