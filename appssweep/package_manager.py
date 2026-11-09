"""Fedora package-manager discovery and cleanup previews.

No installed package files are deleted by AppSweep directly. Package removal
and cache cleanup are delegated to DNF/DNF5 or rpm-ostree with argument lists
and explicit confirmation.
"""

from __future__ import annotations

import os
import platform
import re
from dataclasses import dataclass, field
from pathlib import Path

from .filesystem import measure_path
from .models import ActionKind, CleanupItem, ReportOnlyFinding, RiskGroup
from .runner import CommandRunner
from .util import format_command, human_bytes, parse_size

DNF_CACHE_DIRS = (
    "/var/cache/dnf",
    "/var/cache/libdnf5",
    "/var/cache/yum",
)
RPM_OSTREE_CACHE_DIRS = (
    "/var/cache/rpm-ostree",
)
DNF_PREVIEW_TIMEOUT = 30
PROTECTED_KERNEL_COUNT = 2  # running kernel plus a recovery kernel


@dataclass
class PackageManagerInfo:
    kind: str = "none"
    binary: str = ""
    version: str = ""
    ostree: bool = False

    @property
    def label(self) -> str:
        names = {
            "dnf": "DNF",
            "dnf5": "DNF5",
            "rpm-ostree": "rpm-ostree",
            "none": "not detected",
        }
        return names.get(self.kind, self.kind)

    @property
    def available(self) -> bool:
        return bool(self.binary)


@dataclass
class PackageDiscovery:
    manager: PackageManagerInfo
    items: list[CleanupItem] = field(default_factory=list)
    report_only: list[ReportOnlyFinding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class AutoremovePreview:
    ok: bool = False
    package_names: list[str] = field(default_factory=list)
    freed_bytes: int = 0
    text: str = ""
    reason: str = ""
    mentions_kernel: bool = False

    @property
    def has_work(self) -> bool:
        return bool(self.package_names) or self.freed_bytes > 0


def detect_package_manager(
    runner: CommandRunner | None = None,
    *,
    ostree_marker: str = "/run/ostree-booted",
) -> PackageManagerInfo:
    runner = runner or CommandRunner()
    is_ostree = os.path.exists(ostree_marker)
    rpm_ostree = runner.find("rpm-ostree")
    if is_ostree or rpm_ostree:
        if rpm_ostree:
            version_result = runner.run([rpm_ostree, "--version"], timeout=8)
            version = (version_result.stdout or version_result.stderr).strip().splitlines()
            return PackageManagerInfo(
                kind="rpm-ostree",
                binary=rpm_ostree,
                version=version[0] if version else "",
                ostree=True,
            )
        return PackageManagerInfo(kind="rpm-ostree", ostree=True)

    dnf5 = runner.find("dnf5")
    dnf = runner.find("dnf")
    binary = dnf5 or dnf
    if not binary:
        return PackageManagerInfo()
    kind = "dnf5" if dnf5 or os.path.basename(binary) == "dnf5" else "dnf"
    result = runner.run([binary, "--version"], timeout=8)
    first = (result.stdout or result.stderr).strip().splitlines()
    version = first[0] if first else ""
    if os.path.basename(binary) == "dnf" and "dnf5" in version.lower():
        kind = "dnf5"
    return PackageManagerInfo(kind=kind, binary=binary, version=version)


def discover_package_cleanup(
    runner: CommandRunner | None = None,
    *,
    dnf_cache_dirs: tuple[str, ...] = DNF_CACHE_DIRS,
    ostree_cache_dirs: tuple[str, ...] = RPM_OSTREE_CACHE_DIRS,
    allow_metadata_refresh: bool = False,
    enable_autoremove: bool = True,
    enable_kernels: bool = True,
) -> PackageDiscovery:
    runner = runner or CommandRunner()
    info = detect_package_manager(runner)
    discovery = PackageDiscovery(manager=info)

    if info.kind == "rpm-ostree":
        discovery.notes.append(
            "rpm-ostree is managing this system. DNF package cleanup is disabled; "
            "AppSweep uses rpm-ostree's own metadata-cleanup command and does not "
            "touch deployments, rollbacks, or installed applications."
        )
        _discover_rpm_ostree_cleanup(runner, info, discovery, ostree_cache_dirs)
        return discovery

    if not info.available:
        discovery.notes.append(
            "No DNF/DNF5 executable was found, so package cleanup is unavailable."
        )
        return discovery

    discovery.notes.append(
        f"Package manager: {info.label}"
        + (f" ({info.version})" if info.version else "")
        + "."
    )
    cache_size, existing_caches = _measure_many(dnf_cache_dirs)
    if existing_caches:
        details = tuple(
            f"{path} · {human_bytes(_measure_many((path,))[0])}"
            for path in existing_caches
        )
        discovery.items.append(
            CleanupItem(
                key="dnf-cache",
                title="DNF package and metadata cache",
                group=RiskGroup.REBUILD,
                size_bytes=cache_size,
                summary="Downloaded RPMs plus repository metadata.",
                description=(
                    "DNF keeps downloaded packages and repository metadata so "
                    "installs and updates are faster. Fedora can download it again."
                ),
                consequences=(
                    "Clearing this cache does not uninstall anything. The next DNF "
                    "operation may take longer and use network data to refresh metadata."
                ),
                method=(
                    f"Runs {info.label}'s supported cache-clean command. "
                    "AppSweep does not delete package files by hand."
                ),
                action=ActionKind.DNF_CLEAN_CACHE,
                command_args=((info.binary, "clean", "all"),),
                needs_admin=True,
                estimated=False,
                details=details,
                preview=format_command((info.binary, "clean", "all")),
                metadata={"manager": info.kind},
            )
        )
        discovery.notes.append(
            "DNF cache size is measured from its cache directories; actual reclaim "
            "can differ slightly after metadata is rebuilt."
        )
    else:
        discovery.notes.append("No DNF cache directories with data were found.")

    if enable_autoremove:
        current_release = running_kernel_release()
        preview = preview_autoremove(
            runner,
            info,
            current_release=current_release,
            allow_metadata_refresh=allow_metadata_refresh,
        )
        if preview.ok and preview.has_work:
            if preview.mentions_kernel:
                discovery.report_only.append(
                    ReportOnlyFinding(
                        title="Unneeded dependency preview",
                        size_bytes=preview.freed_bytes,
                        detail=preview.text,
                        reason=(
                            "The preview mentions a kernel package. To keep the running "
                            "kernel safe, AppSweep will not automate this removal."
                        ),
                        group=RiskGroup.REBUILD,
                    )
                )
            else:
                excludes = kernel_exclusion_args(current_release)
                action_args = (
                    info.binary,
                    "autoremove",
                    "--assumeyes",
                    *excludes,
                )
                details = tuple(preview.package_names[:20])
                discovery.items.append(
                    CleanupItem(
                        key="dnf-autoremove",
                        title="Unneeded dependency packages",
                        group=RiskGroup.REBUILD,
                        size_bytes=preview.freed_bytes,
                        summary=f"{len(preview.package_names)} package(s) DNF reports as no longer needed."
                        if preview.package_names
                        else "DNF reports unneeded packages.",
                        description=(
                            "DNF determined that these installed packages are not "
                            "required by any remaining package. This is a preview; "
                            "nothing has been removed."
                        ),
                        consequences=(
                            "Those packages will be uninstalled and can normally be "
                            "reinstalled later. The running kernel is explicitly excluded."
                        ),
                        method=(
                            f"Runs {info.label} autoremove after confirmation. "
                            "AppSweep never removes installed packages itself."
                        ),
                        action=ActionKind.DNF_AUTOREMOVE,
                        command_args=(action_args,),
                        needs_admin=True,
                        estimated=True,
                        details=details,
                        preview=preview.text,
                        metadata={
                            "package_names": preview.package_names,
                            "running_kernel": current_release,
                        },
                    )
                )
        elif not preview.ok:
            discovery.notes.append(
                "Could not obtain a DNF autoremove preview"
                + (f": {preview.reason}" if preview.reason else ".")
            )
        elif not preview.has_work:
            discovery.notes.append("DNF autoremove preview found no unneeded packages.")

    if enable_kernels:
        _discover_old_kernels(runner, info, discovery)

    return discovery


def _discover_rpm_ostree_cleanup(
    runner: CommandRunner,
    info: PackageManagerInfo,
    discovery: PackageDiscovery,
    cache_dirs: tuple[str, ...],
) -> None:
    cache_size, existing = _measure_many(cache_dirs)
    if not info.binary:
        discovery.notes.append(
            "This appears to be an rpm-ostree system, but the rpm-ostree command is "
            "not reachable. No package cleanup is offered."
        )
        return
    discovery.items.append(
        CleanupItem(
            key="rpm-ostree-metadata",
            title="rpm-ostree metadata cache",
            group=RiskGroup.REBUILD,
            size_bytes=cache_size,
            summary="Cached rpm-ostree repository metadata." if existing else "Metadata cleanup through rpm-ostree.",
            description=(
                "rpm-ostree caches repository metadata for updates. The safe cleanup "
                "path is rpm-ostree's metadata cleanup, not manual file deletion."
            ),
            consequences=(
                "Does not remove deployments, rollbacks, or installed applications. "
                "The next update check may need to download metadata again."
            ),
            method="Runs rpm-ostree cleanup -m. This is metadata-only cleanup.",
            action=ActionKind.RPM_OSTREE_METADATA,
            command_args=((info.binary, "cleanup", "-m"),),
            needs_admin=True,
            estimated=True,
            details=tuple(existing),
            preview=format_command((info.binary, "cleanup", "-m")),
        )
    )
    discovery.notes.append(
        "rpm-ostree deployment cleanup, rollback removal, and package layering "
        "changes are deliberately not automated here."
    )


def preview_autoremove(
    runner: CommandRunner,
    info: PackageManagerInfo,
    *,
    current_release: str = "",
    allow_metadata_refresh: bool = False,
) -> AutoremovePreview:
    if not info.binary or info.kind == "rpm-ostree":
        return AutoremovePreview(reason="DNF is not available")
    base_args = [info.binary, "autoremove", "--assumeno"]
    cache_args = [*base_args, "--cacheonly"]
    result = runner.run(cache_args, timeout=DNF_PREVIEW_TIMEOUT)
    if not result.ok and allow_metadata_refresh:
        result = runner.run(base_args, timeout=DNF_PREVIEW_TIMEOUT)
    if not result.ok:
        reason = result.output.strip().splitlines()[-1] if result.output.strip() else "preview command failed"
        if not allow_metadata_refresh:
            reason += " (metadata-only preview; no network refresh was attempted)"
        return AutoremovePreview(ok=False, reason=reason, text=result.output[:4000])

    text = result.output.strip()
    parsed = parse_autoremove_preview(text)
    parsed.ok = True
    if current_release:
        running_name = f"kernel-core-{current_release}"
        parsed.mentions_kernel = parsed.mentions_kernel or any(
            name.startswith("kernel") and current_release in name
            for name in parsed.package_names
        )
        if any(package == f"kernel-core-{current_release}" for package in parsed.package_names):
            parsed.mentions_kernel = True
        if running_name in text:
            parsed.mentions_kernel = True
    return parsed


def parse_autoremove_preview(text: str) -> AutoremovePreview:
    preview = AutoremovePreview(text=text[:4000])
    lowered = text.lower()
    if any(
        marker in lowered
        for marker in (
            "nothing to do",
            "no packages marked for removal",
            "no unused",
            "no matching packages",
        )
    ):
        preview.ok = True
        return preview

    packages: list[str] = []
    in_removing = False
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.lower().startswith("removing"):
            in_removing = True
            # DNF5 writes "Removing:" on its own line.
            continue
        if not in_removing:
            continue
        if stripped.startswith(("Transaction Summary", "Removing dependent")):
            break
        if not stripped or stripped.startswith("-"):
            continue
        fields = stripped.split()
        if len(fields) >= 4 and fields[0] not in {"Package", "Arch"}:
            package = fields[0]
            if re.match(r"^[A-Za-z0-9_.+:-]+$", package):
                packages.append(package)

    if not packages:
        # Classic DNF sometimes prints a compact package table after
        # "Removing:" with package names in the first whitespace field.
        for line in text.splitlines():
            if re.search(r"\bRemoving\b", line, re.IGNORECASE) and ":" in line:
                continue
            match = re.match(r"\s+([A-Za-z0-9_.+-]+)\.(?:x86_64|aarch64|noarch|i686)\s+", line)
            if match:
                packages.append(match.group(1))

    # Keep order and remove duplicates.
    packages = list(dict.fromkeys(packages))
    preview.package_names = packages
    size_match = re.search(
        r"(?:Freed space|Total size|Size):\s*([\d.]+)\s*([KMGTPE]?i?B)",
        text,
        re.IGNORECASE,
    )
    if size_match:
        try:
            preview.freed_bytes = parse_size(
                f"{size_match.group(1)}{size_match.group(2)}"
            )
        except ValueError:
            preview.freed_bytes = 0
    preview.mentions_kernel = any(
        name in {"kernel", "kernel-core"} or name.startswith("kernel-")
        for name in packages
    )
    preview.ok = True
    return preview


def running_kernel_release() -> str:
    release = platform.release()
    # RPM kernel versions do not carry the architecture suffix.
    for arch in ("x86_64", "aarch64", "ppc64le", "s390x", "i686"):
        suffix = "." + arch
        if release.endswith(suffix):
            return release[: -len(suffix)]
    return release


def kernel_exclusion_args(release: str) -> tuple[str, ...]:
    if not release:
        return ()
    names = (
        "kernel",
        "kernel-core",
        "kernel-modules",
        "kernel-modules-core",
        "kernel-modules-extra",
    )
    patterns = ",".join(f"{name}-{release}" for name in names)
    return (f"--exclude={patterns}",)


def _discover_old_kernels(
    runner: CommandRunner,
    info: PackageManagerInfo,
    discovery: PackageDiscovery,
) -> None:
    rpm = runner.find("rpm")
    if not rpm:
        discovery.notes.append(
            "Old-kernel detection needs rpm metadata, but rpm was not found."
        )
        return
    result = runner.run(
        [
            rpm,
            "-qa",
            "--qf",
            "%{NAME}|%{VERSION}-%{RELEASE}|%{ARCH}\n",
        ],
        timeout=20,
    )
    if not result.ok:
        discovery.notes.append("rpm package metadata could not be read.")
        return
    kernel_runtime_names = {
        "kernel",
        "kernel-core",
        "kernel-modules",
        "kernel-modules-core",
        "kernel-modules-extra",
        "kernel-debug",
        "kernel-debug-core",
        "kernel-debug-modules",
        "kernel-debug-modules-core",
        "kernel-debug-modules-extra",
    }
    versions: dict[str, dict[str, object]] = {}
    for line in result.stdout.splitlines():
        fields = line.split("|")
        if len(fields) != 3:
            continue
        name, version, arch = fields
        if name not in kernel_runtime_names:
            continue
        entry = versions.setdefault(
            version, {"names": set(), "arch": arch, "boot_release": ""}
        )
        entry["names"].add(f"{name}-{version}.{arch}")  # type: ignore[union-attr]
        if name == "kernel-core":
            entry["boot_release"] = f"{version}.{arch}"

    current = running_kernel_release()
    all_versions = sorted(versions.keys(), key=version_sort_key, reverse=True)
    if not all_versions:
        discovery.report_only.append(
            ReportOnlyFinding(
                title="Old kernels",
                size_bytes=0,
                detail="No kernel packages were found in rpm metadata.",
                reason="Nothing safe to remove could be identified.",
                group=RiskGroup.REBUILD,
            )
        )
        return

    newest = all_versions[:PROTECTED_KERNEL_COUNT]
    protected = set(newest)
    if current:
        protected.add(current)
    removable = [
        version
        for version in all_versions
        if version not in protected and current not in version
    ]
    if not removable:
        discovery.notes.append(
            f"Keeping the running kernel and {PROTECTED_KERNEL_COUNT - 1} recovery kernel(s); "
            "no older kernel is offered."
        )
        return

    boot_files = Path("/boot")
    module_base = Path("/usr/lib/modules")
    total_size = 0
    details: list[str] = []
    package_args: list[str] = []
    boot_releases: set[str] = set()
    for version in removable:
        entry = versions[version]
        release = str(entry.get("boot_release") or "")
        if release:
            boot_releases.add(release)
            module_dir = module_base / release
            if module_dir.exists():
                measurement = measure_path(str(module_dir))
                total_size += measurement.logical_size
                details.append(f"kernel {version} modules · {human_bytes(measurement.logical_size)}")
        for file_name in (
            f"vmlinuz-{release}",
            f"initramfs-{release}.img",
            f"initrd-{release}.img",
            f"config-{release}",
            f"System.map-{release}",
            f"symvers-{release}.gz",
        ):
            path = boot_files / file_name
            try:
                if path.is_file() and not path.is_symlink():
                    total_size += path.stat().st_size
            except OSError:
                continue
        package_args.extend(sorted(entry.get("names", set())))
        details.append(f"kernel {version}")

    if not package_args:
        return
    command = (
        info.binary,
        "remove",
        "--assumeyes",
        *package_args,
    )
    discovery.items.append(
        CleanupItem(
            key="old-kernels",
            title="Old kernel packages",
            group=RiskGroup.REBUILD,
            size_bytes=total_size,
            summary=(
                f"{len(removable)} old kernel version(s); running kernel and "
                f"{PROTECTED_KERNEL_COUNT - 1} recovery kernel are kept."
            ),
            description=(
                "Older installed kernel packages are still on disk. Removing them "
                "keeps the currently running kernel plus a recovery kernel."
            ),
            consequences=(
                "You will not be able to boot those older kernel versions. "
                "The running kernel and one recovery kernel are never included."
            ),
            method=(
                f"Removes only the listed old kernel packages through {info.label}. "
                "No kernel files are deleted directly."
            ),
            action=ActionKind.DNF_OLD_KERNELS,
            command_args=(command,),
            needs_admin=True,
            estimated=True,
            details=tuple(details),
            preview=format_command(command),
            metadata={"versions": removable, "package_args": package_args},
        )
    )
    discovery.notes.append(
        "Old-kernel size is an estimate from /usr/lib/modules and /boot; package "
        "removal may reclaim a slightly different amount."
    )


def version_sort_key(value: str) -> tuple:
    parts = re.split(r"([0-9]+)", value)
    key: list[tuple[int, int | str]] = []
    for part in parts:
        if part.isdigit():
            key.append((1, int(part)))
        elif part:
            key.append((0, part))
    return tuple(key)


def _measure_many(paths: tuple[str, ...]) -> tuple[int, list[str]]:
    total = 0
    existing: list[str] = []
    for raw in paths:
        path = os.path.abspath(os.path.expanduser(raw))
        if not os.path.exists(path):
            continue
        measurement = measure_path(path)
        if measurement.logical_size <= 0:
            continue
        total += measurement.logical_size
        existing.append(path)
    return total, existing


def package_manager_info_dict(info: PackageManagerInfo) -> dict[str, object]:
    return {
        "kind": info.kind,
        "label": info.label,
        "binary": info.binary,
        "version": info.version,
        "ostree": info.ostree,
        "available": info.available,
    }
