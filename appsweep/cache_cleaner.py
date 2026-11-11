"""User and application cache discovery.

Caches are only cleared after explicit selection. AppSweep clears the contents
of a selected cache folder while leaving the folder itself in place, and it
never clears the top-level ~/.cache directory as a single action.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .filesystem import measure_path
from .models import ActionKind, CleanupItem, ReportOnlyFinding, RiskGroup
from .util import human_bytes

MIN_CACHE_ITEM_SIZE = 1 * 1024 * 1024
KNOWN_CACHE_OWNERS = {
    "mozilla": ("Firefox cache", "Firefox browser cache"),
    "google-chrome": ("Google Chrome cache", "Google Chrome browser cache"),
    "chromium": ("Chromium cache", "Chromium browser cache"),
    "BraveSoftware": ("Brave cache", "Brave browser cache"),
    "microsoft-edge": ("Microsoft Edge cache", "Microsoft Edge browser cache"),
    "thumbnails": ("Thumbnail cache", "Desktop thumbnail previews"),
    "pip": ("pip download cache", "Python package download cache"),
    "flatpak": ("Flatpak user cache", "Flatpak user-level cache"),
    "Code": ("VS Code cache", "Visual Studio Code cache"),
    "JetBrains": ("JetBrains cache", "JetBrains IDE cache"),
    "fontconfig": ("Font cache", "Font configuration cache"),
}


@dataclass
class CacheDiscovery:
    items: list[CleanupItem] = field(default_factory=list)
    report_only: list[ReportOnlyFinding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    cache_roots: list[str] = field(default_factory=list)
    total_size: int = 0


def discover_caches(
    home: str | None = None,
    *,
    include_system_analysis: bool = True,
    min_item_size: int = MIN_CACHE_ITEM_SIZE,
) -> CacheDiscovery:
    home_path = Path(os.path.expanduser(home or str(Path.home()))).absolute()
    discovery = CacheDiscovery()
    cache_home = Path(
        os.environ.get("XDG_CACHE_HOME", str(home_path / ".cache"))
    ).expanduser()
    try:
        cache_home = cache_home.absolute()
    except OSError:
        cache_home = home_path / ".cache"

    _discover_user_cache_entries(cache_home, discovery, min_item_size)
    _discover_flatpak_app_caches(home_path, discovery, min_item_size)

    if include_system_analysis:
        _analyze_system_cache(discovery)

    discovery.items.sort(key=lambda item: (-item.size_bytes, item.title))
    discovery.cache_roots = [path for item in discovery.items for path in item.paths]
    discovery.total_size = sum(item.size_bytes for item in discovery.items)
    if discovery.items:
        discovery.notes.append(
            "Selected cache folders keep the folder itself; only their contents are "
            "removed. Apps may regenerate the cache, and some may need to be closed first."
        )
    return discovery


def _discover_user_cache_entries(
    cache_home: Path,
    discovery: CacheDiscovery,
    min_item_size: int,
) -> None:
    if cache_home.is_symlink():
        discovery.notes.append(
            f"{cache_home} is a symbolic link; AppSweep will not clear it."
        )
        return
    if not cache_home.is_dir():
        discovery.notes.append("No user cache folder was found.")
        return

    try:
        entries = sorted(os.scandir(cache_home), key=lambda e: e.name.lower())
    except OSError as exc:
        discovery.notes.append(f"Could not read {cache_home}: {exc.strerror or exc}")
        return

    small_total = 0
    small_count = 0
    for entry in entries:
        try:
            lstat = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if entry.is_symlink():
            continue
        if not (entry.is_dir(follow_symlinks=False) or entry.is_file(follow_symlinks=False)):
            continue
        if lstat.st_uid != os.getuid() and lstat.st_uid != 0:
            continue
        measurement = measure_path(entry.path)
        if measurement.logical_size < min_item_size:
            small_total += measurement.logical_size
            small_count += 1
            continue
        owner_title, owner_description = _owner_for_cache(entry.name)
        _append_cache_item(
            discovery,
            path=entry.path,
            title=owner_title,
            description=owner_description,
            size=measurement.logical_size,
            file_count=measurement.files,
            incomplete=measurement.incomplete,
            group_name=None,
        )

    if small_count:
        discovery.report_only.append(
            ReportOnlyFinding(
                title="Small user cache entries",
                size_bytes=small_total,
                detail=(
                    f"{small_count} cache entr{'y' if small_count == 1 else 'ies'} "
                    f"below {human_bytes(min_item_size)} each."
                ),
                reason="Only meaningful cache entries are offered as selectable cleanup.",
                group=RiskGroup.SAFE,
            )
        )


def _discover_flatpak_app_caches(
    home_path: Path,
    discovery: CacheDiscovery,
    min_item_size: int,
) -> None:
    apps_dir = home_path / ".var" / "app"
    if not apps_dir.is_dir() or apps_dir.is_symlink():
        return
    try:
        app_dirs = sorted(apps_dir.iterdir(), key=lambda p: p.name.lower())
    except OSError as exc:
        discovery.notes.append(
            f"Could not inspect Flatpak app caches: {exc.strerror or exc}"
        )
        return
    for app_dir in app_dirs:
        try:
            if not app_dir.is_dir() or app_dir.is_symlink():
                continue
            cache_dir = app_dir / "cache"
            if not cache_dir.is_dir() or cache_dir.is_symlink():
                continue
        except OSError:
            continue
        measurement = measure_path(str(cache_dir))
        if measurement.logical_size < min_item_size:
            continue
        _append_cache_item(
            discovery,
            path=str(cache_dir),
            title=f"Flatpak app cache · {app_dir.name}",
            description=f"Cache data generated by Flatpak app {app_dir.name}",
            size=measurement.logical_size,
            file_count=measurement.files,
            incomplete=measurement.incomplete,
            group_name=app_dir.name,
        )


def _append_cache_item(
    discovery: CacheDiscovery,
    *,
    path: str,
    title: str,
    description: str,
    size: int,
    file_count: int,
    incomplete: bool,
    group_name: str | None,
) -> None:
    normalized = os.path.normpath(os.path.abspath(path))
    details = [
        f"{file_count:,} file(s)",
        "Folder is kept; contents are cleared",
    ]
    if incomplete:
        details.append("Some files could not be read, so the size is a lower bound")
    discovery.items.append(
        CleanupItem(
            key="cache:" + normalized,
            title=title,
            group=RiskGroup.SAFE,
            size_bytes=size,
            summary=description + ".",
            description=(
                f"Cache folder: {normalized}. AppSweep measured its contents without "
                "reading file data."
            ),
            consequences=(
                "The app may be slower for a short time while it rebuilds the cache. "
                "Close the related application if possible. Personal documents and app "
                "settings are not inside a cache by design, but check the path above if "
                "you are unsure."
            ),
            method=(
                "Removes the contents of this cache folder, never the folder itself. "
                "Symbolic links are unlinked, not followed."
            ),
            action=ActionKind.CLEAR_CACHE_CONTENTS,
            paths=(normalized,),
            needs_admin=False,
            estimated=incomplete,
            details=tuple(details),
            preview=(
                f"Delete contents inside {normalized}\n"
                "Keep the folder and do not follow symbolic links."
            ),
            metadata={"owner": group_name or title, "file_count": file_count},
        )
    )


def _owner_for_cache(name: str) -> tuple[str, str]:
    if name in KNOWN_CACHE_OWNERS:
        title, description = KNOWN_CACHE_OWNERS[name]
        return title, description
    if name.startswith("."):
        clean = name.lstrip(".")
    else:
        clean = name
    return f"Application cache · {clean}", f"Unrecognized cache folder from {clean}"


def _analyze_system_cache(discovery: CacheDiscovery) -> None:
    root = Path("/var/cache")
    if not root.is_dir() or root.is_symlink():
        return
    try:
        entries = list(os.scandir(root))
    except OSError as exc:
        discovery.notes.append(
            f"Could not inspect /var/cache: {exc.strerror or exc}"
        )
        return
    total = 0
    count = 0
    dnf_paths = {"/var/cache/dnf", "/var/cache/libdnf5", "/var/cache/yum"}
    recognized: list[str] = []
    for entry in entries:
        if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
            continue
        if entry.path in dnf_paths:
            continue  # DNF cleanup item owns this size.
        if entry.name in {"PackageKit", "gnome-software", "flatpak", "fwupd", "fontconfig"}:
            measurement = measure_path(entry.path)
            if measurement.logical_size:
                total += measurement.logical_size
                count += 1
                recognized.append(f"{entry.name} · {human_bytes(measurement.logical_size)}")
    if total:
        discovery.report_only.append(
            ReportOnlyFinding(
                title="System cache analysis",
                size_bytes=total,
                detail=(
                    f"{count} root-owned system cache folder(s), including "
                    + ", ".join(recognized[:8])
                ),
                reason=(
                    "AppSweep does not clear root-owned caches directly. Recognized "
                    "package caches use the package manager; other system caches are "
                    "reported for transparency."
                ),
                group=RiskGroup.SAFE,
            )
        )
    discovery.notes.append(
        "System cache folders are analyzed read-only; no root-owned cache folder is "
        "offered for direct recursive deletion."
    )
