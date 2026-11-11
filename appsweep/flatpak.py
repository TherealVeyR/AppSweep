"""Flatpak runtime discovery.

Flatpak 1.18 has no supported non-interactive "preview unused refs" command.
AppSweep therefore builds a conservative candidate list from ``flatpak list``
and labels its size as an estimate. The actual cleanup is delegated to
Flatpak's own ``uninstall --unused`` operation, which re-checks dependencies
and never uninstalls applications. Pinned runtimes are kept by Flatpak.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import ActionKind, CleanupItem, ReportOnlyFinding, RiskGroup
from .runner import CommandRunner
from .util import format_command, human_bytes, parse_size


@dataclass
class FlatpakInfo:
    available: bool = False
    binary: str = ""
    version: str = ""

    @property
    def label(self) -> str:
        return "Flatpak" if self.available else "Flatpak not detected"


@dataclass
class RuntimeEntry:
    ref: str
    size: int
    installation: str


@dataclass
class FlatpakDiscovery:
    info: FlatpakInfo
    items: list[CleanupItem] = field(default_factory=list)
    report_only: list[ReportOnlyFinding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def detect_flatpak(runner: CommandRunner | None = None) -> FlatpakInfo:
    runner = runner or CommandRunner()
    binary = runner.find("flatpak")
    if not binary:
        return FlatpakInfo()
    result = runner.run([binary, "--version"], timeout=8)
    version = (result.stdout or result.stderr).strip().splitlines()
    return FlatpakInfo(
        available=True,
        binary=binary,
        version=version[0] if version else "",
    )


def discover_flatpak(
    runner: CommandRunner | None = None,
    *,
    user_install: bool = True,
    system_install: bool = True,
) -> FlatpakDiscovery:
    runner = runner or CommandRunner()
    info = detect_flatpak(runner)
    discovery = FlatpakDiscovery(info=info)
    if not info.available:
        discovery.notes.append(
            "Flatpak was not detected, so runtime and Flatpak-app cleanup are unavailable."
        )
        return discovery

    discovery.notes.append(
        f"Flatpak detected: {info.version or 'version unknown'}. "
        "Applications themselves are never uninstalled by AppSweep."
    )

    installations: list[tuple[str, bool, bool]] = []
    if user_install:
        installations.append(("user", False, False))
    if system_install:
        installations.append(("system", True, True))

    candidate_total = 0
    for label, system, admin in installations:
        used, used_ok = _list_used_runtimes(runner, info.binary, system)
        runtimes, runtimes_ok = _list_runtimes(runner, info.binary, system)
        if not used_ok:
            discovery.notes.append(
                f"Could not read {label} Flatpak application runtimes; no automatic "
                f"{label} runtime cleanup is offered."
            )
            continue
        if not runtimes_ok:
            discovery.notes.append(
                f"Could not read the {label} Flatpak runtime list; no automatic "
                f"{label} runtime cleanup is offered."
            )
            continue
        candidates = [
            runtime
            for runtime in runtimes
            if runtime.ref not in used and runtime.ref not in _used_aliases(used)
        ]
        if not candidates:
            discovery.notes.append(
                f"No obviously unused {label} Flatpak runtimes were found by preview."
            )
            continue

        size = sum(runtime.size for runtime in candidates)
        candidate_total += size
        details = tuple(f"{r.ref} · {human_bytes(r.size)}" for r in candidates[:25])
        args = (
            info.binary,
            "--system" if system else "--user",
            "uninstall",
            "--unused",
            "--assumeyes",
        )
        discovery.items.append(
            CleanupItem(
                key=f"flatpak-unused-{label}",
                title=f"Unused {label} Flatpak runtimes",
                group=RiskGroup.REBUILD,
                size_bytes=size,
                summary=(
                    f"Preview found {len(candidates)} runtime(s) not referenced by "
                    "an installed Flatpak app. Flatpak makes the final check."
                ),
                description=(
                    "Flatpak keeps runtimes for installed applications. This cleanup "
                    "asks Flatpak to remove only refs that its dependency check marks "
                    "as unused. Applications and their data are not removed."
                ),
                consequences=(
                    "Unused runtimes will be downloaded again if a future application "
                    "needs them. Pinned runtimes are preserved by Flatpak. Moving or "
                    "deleting app data requires a separate manual choice."
                ),
                method=(
                    "Runs Flatpak's official 'uninstall --unused' operation. "
                    "Flatpak re-checks which refs are genuinely unused at that moment."
                ),
                action=ActionKind.FLATPAK_UNUSED,
                command_args=(args,),
                needs_admin=admin,
                estimated=True,
                details=details,
                preview=(
                    format_command(args)
                    + "\nPreview size is an estimate: Flatpak decides the final unused set."
                ),
                metadata={"installation": label, "refs": [r.ref for r in candidates]},
            )
        )

    if candidate_total:
        discovery.notes.append(
            "Flatpak unused-runtime sizes are estimates based on the installed runtime "
            "catalog. Flatpak re-checks dependencies and pinned refs when it runs; "
            "the actual reclaim may be smaller."
        )
    return discovery


def _used_aliases(used: set[str]) -> set[str]:
    aliases: set[str] = set()
    for ref in used:
        aliases.add(ref)
        if ref.startswith("runtime/"):
            aliases.add(ref[len("runtime/") :])
        elif not ref.startswith("app/"):
            aliases.add("runtime/" + ref)
    return aliases


def _list_used_runtimes(
    runner: CommandRunner, binary: str, system: bool
) -> tuple[set[str], bool]:
    args = [
        binary,
        "--system" if system else "--user",
        "list",
        "--app",
        "--columns=application,runtime",
    ]
    result = runner.run(args, timeout=20)
    if not result.ok:
        return set(), False
    used: set[str] = set()
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) < 2:
            fields = line.split()
        for part in fields[1:]:
            value = part.strip()
            if not value or value in {"runtime", "-"}:
                continue
            # The runtime field may be a full ref or just an ID/branch.
            if "/" in value or "." in value:
                used.add(value)
                used.add(value.replace("runtime/", "", 1))
    return used, True


def _list_runtimes(
    runner: CommandRunner, binary: str, system: bool
) -> tuple[list[RuntimeEntry], bool]:
    args = [
        binary,
        "--system" if system else "--user",
        "list",
        "--runtime",
        "--columns=ref,size,installation",
    ]
    result = runner.run(args, timeout=25)
    if not result.ok:
        return [], False
    runtimes: list[RuntimeEntry] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parsed = _parse_runtime_line(line)
        if parsed:
            runtimes.append(parsed)
    return runtimes, True


def _parse_runtime_line(line: str) -> RuntimeEntry | None:
    if "\t" in line:
        fields = [field.strip() for field in line.split("\t") if field.strip()]
    else:
        fields = line.split()
    if len(fields) < 2:
        return None
    ref = fields[0]
    if ref.lower() == "ref":
        return None
    if not (
        ref.startswith("runtime/")
        or "/" in ref
        or "." in ref
    ):
        return None
    size_match = re.search(
        r"([\d.]+)\s*([KMGTPE]?i?B)\b", " ".join(fields[1:]), re.IGNORECASE
    )
    size = 0
    if size_match:
        try:
            size = parse_size(f"{size_match.group(1)}{size_match.group(2)}")
        except ValueError:
            size = 0
    installation = fields[2] if len(fields) > 2 else ""
    return RuntimeEntry(ref=ref, size=size, installation=installation)


def flatpak_info_dict(info: FlatpakInfo) -> dict[str, object]:
    return {
        "available": info.available,
        "binary": info.binary,
        "version": info.version,
    }
