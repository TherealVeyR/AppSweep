"""Read-only host-integration capability detection.

AppSweep must remain useful when optional Fedora host tools are absent. This
module only checks whether commands are visible on PATH; it never runs a
package manager, deletes anything, or changes host configuration.

Automated integration tests use mocked command runners. The capability report
therefore labels real-host execution as unverified until the manual checklist
in ``INTEGRATIONS.md`` has been completed on a real Fedora host.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .runner import CommandRunner

VERIFICATION_NOTE = (
    "Automated integration coverage uses mocked tools; real Fedora execution "
    "is unverified."
)


@dataclass(frozen=True)
class ToolCapability:
    """One optional host command and the cleanup feature that depends on it."""

    key: str
    name: str
    command: str
    purpose: str
    admin: bool = False
    available: bool = False
    path: str = ""
    verification: str = VERIFICATION_NOTE

    @property
    def status(self) -> str:
        return "available" if self.available else "missing"

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "name": self.name,
            "command": self.command,
            "purpose": self.purpose,
            "admin": self.admin,
            "available": self.available,
            "path": self.path,
            "status": self.status,
            "verification": self.verification,
        }


TOOL_SPECS = (
    ToolCapability(
        key="dnf",
        name="DNF",
        command="dnf",
        purpose="Package cache cleanup, autoremove preview, and old-kernel removal.",
    ),
    ToolCapability(
        key="dnf5",
        name="DNF5",
        command="dnf5",
        purpose="Package cache cleanup, autoremove preview, and old-kernel removal.",
    ),
    ToolCapability(
        key="rpm-ostree",
        name="rpm-ostree",
        command="rpm-ostree",
        purpose="Metadata-only cleanup on immutable Fedora systems.",
        admin=True,
    ),
    ToolCapability(
        key="rpm",
        name="RPM",
        command="rpm",
        purpose="Read package metadata for safe old-kernel detection.",
    ),
    ToolCapability(
        key="flatpak",
        name="Flatpak",
        command="flatpak",
        purpose="Unused-runtime discovery and Flatpak's official unused cleanup.",
    ),
    ToolCapability(
        key="journalctl",
        name="journalctl",
        command="journalctl",
        purpose="Journal size measurement and supported vacuum cleanup.",
        admin=True,
    ),
    ToolCapability(
        key="pkexec",
        name="pkexec",
        command="pkexec",
        purpose="Per-command administrator approval for system cleanup operations.",
        admin=True,
    ),
    ToolCapability(
        key="gio",
        name="gio",
        command="gio",
        purpose="Moving personal files to Trash and emptying the desktop Trash.",
    ),
)


@dataclass(frozen=True)
class CapabilityReport:
    """Detected host tools plus actionable, non-blocking warnings."""

    tools: tuple[ToolCapability, ...] = ()

    @property
    def available_tools(self) -> tuple[ToolCapability, ...]:
        return tuple(tool for tool in self.tools if tool.available)

    @property
    def missing_tools(self) -> tuple[ToolCapability, ...]:
        return tuple(tool for tool in self.tools if not tool.available)

    def tool(self, key: str) -> ToolCapability | None:
        return next((tool for tool in self.tools if tool.key == key), None)

    @property
    def warnings(self) -> list[str]:
        """Return actionable notes for missing tools.

        Missing optional tools are not errors: the scan and unrelated cleanup
        categories continue. Each warning explains exactly what is unavailable
        and what still works.
        """

        if not self.tools:
            return []

        available = {tool.key for tool in self.available_tools}
        warnings: list[str] = []
        package_managers = {"dnf", "dnf5", "rpm-ostree"}

        if not available.intersection(package_managers):
            warnings.append(
                "Package cleanup is unavailable because DNF, DNF5, and "
                "rpm-ostree were not found. Storage scanning and non-package "
                "cleanup still work."
            )
        elif not available.intersection({"dnf", "dnf5"}) and "rpm" not in available:
            warnings.append(
                "Old-kernel detection is unavailable because rpm was not found. "
                "rpm-ostree metadata cleanup can still work."
            )
        elif available.intersection({"dnf", "dnf5"}) and "rpm" not in available:
            warnings.append(
                "Old-kernel removal is unavailable because rpm was not found. "
                "DNF cache cleanup and autoremove preview can still work."
            )

        if "flatpak" not in available:
            warnings.append(
                "Flatpak cleanup is unavailable because flatpak was not found. "
                "Install Flatpak through Fedora only if you use Flatpak apps; "
                "storage scanning still works."
            )
        if "journalctl" not in available:
            warnings.append(
                "Journal vacuum is unavailable because journalctl was not found. "
                "Journal storage is measured read-only and is never deleted directly."
            )
        if "pkexec" not in available:
            warnings.append(
                "Administrator-approved cleanup is unavailable because pkexec was "
                "not found. Package, journal, and system actions will be skipped; "
                "cache cleanup still works."
            )
        if "gio" not in available:
            warnings.append(
                "Trash move and emptying are unavailable because gio was not found. "
                "AppSweep will not permanently delete personal files as a fallback."
            )
        return warnings

    @property
    def real_host_verified(self) -> bool:
        # Do not flip this in a mock-only change. It may become true only when
        # the checklist evidence in INTEGRATIONS.md is recorded for a release.
        return False

    def to_dict(self) -> dict[str, object]:
        return {
            "tools": [tool.to_dict() for tool in self.tools],
            "warnings": self.warnings,
            "real_host_verified": self.real_host_verified,
            "verification": VERIFICATION_NOTE,
        }


def detect_capabilities(
    runner: CommandRunner | None = None,
) -> CapabilityReport:
    """Check PATH visibility for optional host tools without executing them."""

    runner = runner or CommandRunner()
    tools = []
    for spec in TOOL_SPECS:
        path = runner.find(spec.command)
        tools.append(replace(spec, available=bool(path), path=path or ""))
    return CapabilityReport(tools=tuple(tools))
