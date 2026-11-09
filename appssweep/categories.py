"""Combine independent cleanup modules into one stable, numbered report."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from .cache_cleaner import discover_caches
from .flatpak import FlatpakInfo, discover_flatpak
from .log_cleaner import discover_logs
from .models import CleanupItem, ReportOnlyFinding, RiskGroup, ScanResult
from .package_manager import (
    PackageManagerInfo,
    discover_package_cleanup,
)
from .runner import CommandRunner
from .trash_manager import discover_trash

CATEGORY_CHOICES = ("packages", "flatpak", "cache", "logs", "trash")
DEFAULT_CATEGORIES = frozenset(CATEGORY_CHOICES)
GROUP_ORDER = {
    RiskGroup.SAFE: 0,
    RiskGroup.CAREFUL: 1,
    RiskGroup.REBUILD: 2,
}


@dataclass
class CleanupDiscovery:
    items: list[CleanupItem] = field(default_factory=list)
    report_only: list[ReportOnlyFinding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    cache_roots: list[str] = field(default_factory=list)
    categories_run: list[str] = field(default_factory=list)
    package_info: PackageManagerInfo | None = None
    flatpak_info: FlatpakInfo | None = None
    modules: dict[str, object] = field(default_factory=dict)

    @property
    def total_size(self) -> int:
        return sum(item.size_bytes for item in self.items if item.eligible)

    @property
    def by_group(self) -> dict[RiskGroup, list[CleanupItem]]:
        return {
            group: [item for item in self.items if item.group == group]
            for group in RiskGroup
        }


def discover_cleanup(
    scan: ScanResult,
    *,
    categories: Iterable[str] | None = None,
    runner: CommandRunner | None = None,
    allow_metadata_refresh: bool = False,
    include_system_analysis: bool = True,
    home: str | None = None,
) -> CleanupDiscovery:
    """Run enabled cleanup discovery modules without deleting anything."""

    runner = runner or CommandRunner()
    selected = set(categories) if categories is not None else set(DEFAULT_CATEGORIES)
    unknown = selected - set(CATEGORY_CHOICES)
    if unknown:
        raise ValueError(f"unknown cleanup categories: {', '.join(sorted(unknown))}")
    discovery = CleanupDiscovery()

    if "packages" in selected:
        package_discovery = discover_package_cleanup(
            runner,
            allow_metadata_refresh=allow_metadata_refresh,
        )
        discovery.modules["packages"] = package_discovery
        discovery.package_info = package_discovery.manager
        discovery.items.extend(package_discovery.items)
        discovery.report_only.extend(package_discovery.report_only)
        discovery.notes.extend(package_discovery.notes)
        discovery.categories_run.append("packages")

    if "flatpak" in selected:
        flatpak_discovery = discover_flatpak(runner)
        discovery.modules["flatpak"] = flatpak_discovery
        discovery.flatpak_info = flatpak_discovery.info
        discovery.items.extend(flatpak_discovery.items)
        discovery.report_only.extend(flatpak_discovery.report_only)
        discovery.notes.extend(flatpak_discovery.notes)
        discovery.categories_run.append("flatpak")

    if "cache" in selected:
        cache_discovery = discover_caches(
            home,
            include_system_analysis=include_system_analysis,
        )
        discovery.modules["cache"] = cache_discovery
        discovery.items.extend(cache_discovery.items)
        discovery.report_only.extend(cache_discovery.report_only)
        discovery.notes.extend(cache_discovery.notes)
        discovery.cache_roots.extend(cache_discovery.cache_roots)
        discovery.categories_run.append("cache")

    if "logs" in selected:
        log_discovery = discover_logs(runner)
        discovery.modules["logs"] = log_discovery
        discovery.items.extend(log_discovery.items)
        discovery.report_only.extend(log_discovery.report_only)
        discovery.notes.extend(log_discovery.notes)
        discovery.categories_run.append("logs")

    if "trash" in selected:
        trash_discovery = discover_trash(runner)
        discovery.modules["trash"] = trash_discovery
        discovery.items.extend(trash_discovery.items)
        discovery.report_only.extend(trash_discovery.report_only)
        discovery.notes.extend(trash_discovery.notes)
        discovery.categories_run.append("trash")

    if scan.cancelled:
        for item in discovery.items:
            if item.eligible:
                item.eligible = False
                item.report_only_reason = "Scan was cancelled, so cleanup is disabled."
        discovery.notes.append(
            "The scan was cancelled. Cleanup actions are disabled in this report."
        )

    discovery.items = _dedupe_and_number(discovery.items)
    discovery.report_only = _dedupe_findings(discovery.report_only)
    discovery.cache_roots = sorted(set(discovery.cache_roots))
    return discovery


def _dedupe_and_number(items: list[CleanupItem]) -> list[CleanupItem]:
    seen: set[str] = set()
    unique: list[CleanupItem] = []
    for item in items:
        if item.key in seen:
            continue
        seen.add(item.key)
        unique.append(item)
    unique.sort(key=lambda item: (GROUP_ORDER[item.group], -item.size_bytes, item.title))
    for index, item in enumerate(unique, start=1):
        item.number = index
    return unique


def _dedupe_findings(findings: list[ReportOnlyFinding]) -> list[ReportOnlyFinding]:
    seen: set[tuple[str, int]] = set()
    unique: list[ReportOnlyFinding] = []
    for finding in findings:
        key = (finding.title, finding.size_bytes)
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    unique.sort(key=lambda finding: (-finding.size_bytes, finding.title))
    return unique


def category_summary(discovery: CleanupDiscovery) -> str:
    if not discovery.items:
        return "No selectable cleanup found in the enabled categories."
    parts = [
        f"{len(items)} {group.value}"
        for group, items in discovery.by_group.items()
        if items
    ]
    return ", ".join(parts)
