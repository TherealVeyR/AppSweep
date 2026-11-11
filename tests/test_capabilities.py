"""Capability detection tests.

These tests use a fake command runner. They verify that missing host tools are
reported clearly without executing host commands or failing the application.
The tests are mocks, not real Fedora integration coverage.
"""

from __future__ import annotations

import unittest

from appsweep.capabilities import (
    TOOL_SPECS,
    CapabilityReport,
    detect_capabilities,
)

from .helpers import FakeRunner


def all_tools() -> dict[str, str]:
    return {tool.command: f"/usr/bin/{tool.command}" for tool in TOOL_SPECS}


class CapabilityDetectionTests(unittest.TestCase):
    def test_all_tools_available_has_no_warnings(self) -> None:
        report = detect_capabilities(FakeRunner(mapping=all_tools()))

        self.assertEqual(len(report.available_tools), len(TOOL_SPECS))
        self.assertEqual(report.missing_tools, ())
        self.assertEqual(report.warnings, [])
        self.assertFalse(report.real_host_verified)

    def test_detection_checks_path_without_running_commands(self) -> None:
        fake = FakeRunner(mapping={"dnf": "/usr/bin/dnf"})
        detect_capabilities(fake)

        self.assertEqual(fake.calls, [])
        self.assertIn("dnf", fake.which_calls)
        self.assertIn("gio", fake.which_calls)

    def test_missing_tools_produce_actionable_nonfatal_warnings(self) -> None:
        report = detect_capabilities(FakeRunner(mapping={}))
        warnings = " ".join(report.warnings)

        self.assertEqual(len(report.missing_tools), len(TOOL_SPECS))
        self.assertIn("Package cleanup is unavailable", warnings)
        self.assertIn("Flatpak cleanup is unavailable", warnings)
        self.assertIn("Journal vacuum is unavailable", warnings)
        self.assertIn("pkexec", warnings)
        self.assertIn("gio", warnings)
        self.assertIn("never deleted directly", warnings)
        self.assertIn("will not permanently delete", warnings)
        self.assertIn("mocked", report.to_dict()["verification"])
        self.assertFalse(report.to_dict()["real_host_verified"])

    def test_dnf5_only_does_not_warn_about_missing_dnf(self) -> None:
        mapping = all_tools()
        del mapping["dnf"]
        del mapping["rpm-ostree"]

        report = detect_capabilities(FakeRunner(mapping=mapping))
        dnf5 = report.tool("dnf5")
        dnf = report.tool("dnf")

        self.assertIsNotNone(dnf5)
        self.assertIsNotNone(dnf)
        assert dnf5 is not None
        assert dnf is not None
        self.assertTrue(dnf5.available)
        self.assertFalse(dnf.available)
        self.assertNotIn(
            "DNF, DNF5, and rpm-ostree were not found", " ".join(report.warnings)
        )

    def test_dnf_without_rpm_explains_old_kernel_limit(self) -> None:
        mapping = all_tools()
        del mapping["rpm"]

        report = detect_capabilities(FakeRunner(mapping=mapping))

        self.assertIn("Old-kernel removal is unavailable", " ".join(report.warnings))

    def test_rpm_ostree_without_rpm_does_not_claim_old_kernel_removal(self) -> None:
        mapping = all_tools()
        del mapping["rpm"]
        del mapping["dnf"]
        del mapping["dnf5"]

        report = detect_capabilities(FakeRunner(mapping=mapping))

        self.assertIn("rpm-ostree metadata cleanup", " ".join(report.warnings))
        self.assertNotIn("Old-kernel removal is unavailable", " ".join(report.warnings))

    def test_empty_report_is_silent(self) -> None:
        report = CapabilityReport()

        self.assertEqual(report.warnings, [])
        self.assertEqual(report.to_dict()["tools"], [])


if __name__ == "__main__":
    unittest.main()
