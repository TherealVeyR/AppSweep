"""Mocked host-tool integration tests.

The development environment does not expose dnf, rpm-ostree, rpm, or
journalctl. Every command in this file is handled by ``FakeRunner``; these are
mocked integration tests, not evidence that the commands work on a real Fedora
host. Real-host validation is documented in ``INTEGRATIONS.md``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from appsweep.flatpak import discover_flatpak
from appsweep.log_cleaner import discover_logs, parse_journal_disk_usage
from appsweep.models import ActionKind
from appsweep.package_manager import (
    discover_package_cleanup,
    running_kernel_release,
)
from appsweep.runner import CommandResult

from .helpers import FakeRunner, write_file


def result(args, code: int = 0, stdout: str = "", stderr: str = "") -> CommandResult:
    return CommandResult(
        args=tuple(str(arg) for arg in args),
        returncode=code,
        stdout=stdout,
        stderr=stderr,
    )


class MockedHostIntegrationTests(unittest.TestCase):
    """Exercise host command parsing and fallback paths using mocks only."""

    def test_missing_package_manager_is_nonfatal(self) -> None:
        discovery = discover_package_cleanup(
            FakeRunner(mapping={}),
            dnf_cache_dirs=(),
            ostree_cache_dirs=(),
        )

        self.assertEqual(discovery.items, [])
        self.assertFalse(discovery.manager.available)
        self.assertTrue(
            any("No DNF/DNF5 executable" in note for note in discovery.notes)
        )

    def test_missing_flatpak_is_nonfatal(self) -> None:
        discovery = discover_flatpak(FakeRunner(mapping={}))

        self.assertFalse(discovery.info.available)
        self.assertEqual(discovery.items, [])
        self.assertTrue(
            any("Flatpak was not detected" in note for note in discovery.notes)
        )

    def test_rpm_ostree_metadata_discovery_uses_a_mocked_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "rpm-ostree"
            cache.mkdir()
            write_file(cache / "metadata.bin", 4096)

            def handler(args, needs_admin, timeout):
                if args[-1] == "--version":
                    return result(args, stdout="rpm-ostree 2024.5\n")
                return result(args, code=1, stderr="unexpected command")

            fake = FakeRunner(
                mapping={"rpm-ostree": "/usr/bin/rpm-ostree"},
                handler=handler,
            )
            discovery = discover_package_cleanup(
                fake,
                dnf_cache_dirs=(),
                ostree_cache_dirs=(str(cache),),
            )

        self.assertEqual(discovery.manager.kind, "rpm-ostree")
        self.assertEqual(len(discovery.items), 1)
        item = discovery.items[0]
        self.assertEqual(item.key, "rpm-ostree-metadata")
        self.assertEqual(item.action, ActionKind.RPM_OSTREE_METADATA)
        self.assertEqual(item.command_args, (("/usr/bin/rpm-ostree", "cleanup", "-m"),))
        self.assertGreater(item.size_bytes, 0)

    def test_old_kernel_discovery_uses_mocked_rpm_metadata(self) -> None:
        current = running_kernel_release()
        rpm_output = "\n".join(
            (
                f"kernel-core|{current}|x86_64",
                "kernel-core|0.0.1-1.fc40|x86_64",
                "kernel-core|0.0.2-1.fc40|x86_64",
            )
        )

        def handler(args, needs_admin, timeout):
            if tuple(args) == ("/usr/bin/dnf", "--version"):
                return result(args, stdout="4.19.0\n")
            if tuple(args[:2]) == ("/usr/bin/rpm", "-qa"):
                return result(args, stdout=rpm_output)
            return result(args, code=1, stderr="unexpected command")

        fake = FakeRunner(
            mapping={"dnf": "/usr/bin/dnf", "rpm": "/usr/bin/rpm"},
            handler=handler,
        )
        discovery = discover_package_cleanup(
            fake,
            dnf_cache_dirs=(),
            enable_autoremove=False,
        )

        item = next(
            (entry for entry in discovery.items if entry.key == "old-kernels"), None
        )
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.action, ActionKind.DNF_OLD_KERNELS)
        versions = list(item.metadata["versions"])
        self.assertIn("0.0.1-1.fc40", versions)
        self.assertNotIn("0.0.2-1.fc40", versions)
        self.assertNotIn(current, versions)
        command = item.command_args[0]
        self.assertEqual(command[:3], ("/usr/bin/dnf", "remove", "--assumeyes"))
        self.assertTrue(any("kernel-core-0.0.1-1.fc40" in arg for arg in command))

    def test_journal_usage_parser_accepts_short_and_iec_units(self) -> None:
        self.assertEqual(
            parse_journal_disk_usage("take up 1.0G in the file system"),
            1024**3,
        )
        self.assertEqual(
            parse_journal_disk_usage("take up 1.0GiB in the file system"),
            1024**3,
        )

    def test_journal_without_journalctl_stays_report_only(self) -> None:
        with (
            patch(
                "appsweep.log_cleaner._measure_journal_directories",
                return_value=300 * 1024**2,
            ),
            patch("appsweep.log_cleaner._analyze_rotated_logs"),
            patch("appsweep.log_cleaner._analyze_crash_dumps"),
            patch("appsweep.log_cleaner._analyze_temporary_files"),
        ):
            discovery = discover_logs(
                FakeRunner(mapping={}),
                include_temporary=False,
            )

        self.assertEqual(discovery.items, [])
        self.assertEqual(len(discovery.report_only), 1)
        finding = discovery.report_only[0]
        self.assertEqual(finding.title, "System journal")
        self.assertIn("journalctl was not found", finding.reason)
        self.assertIn("will not manually delete", finding.reason)

    def test_journal_vacuum_uses_a_mocked_journalctl(self) -> None:
        def handler(args, needs_admin, timeout):
            return result(
                args,
                stdout="Archived and active journals take up 1.0G in the file system.\n",
            )

        fake = FakeRunner(
            mapping={"journalctl": "/usr/bin/journalctl"},
            handler=handler,
        )
        with (
            patch("appsweep.log_cleaner._measure_journal_directories", return_value=0),
            patch("appsweep.log_cleaner._analyze_rotated_logs"),
            patch("appsweep.log_cleaner._analyze_crash_dumps"),
            patch("appsweep.log_cleaner._analyze_temporary_files"),
        ):
            discovery = discover_logs(fake, include_temporary=False)

        self.assertEqual(len(discovery.items), 1)
        item = discovery.items[0]
        self.assertEqual(item.action, ActionKind.JOURNAL_VACUUM)
        self.assertEqual(
            item.command_args,
            (("/usr/bin/journalctl", "--vacuum-size=256M"),),
        )
        self.assertGreater(item.size_bytes, 0)


if __name__ == "__main__":
    unittest.main()
