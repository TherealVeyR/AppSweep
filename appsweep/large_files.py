"""Personal large-file rules and quick-scan locations.

Personal files are always classified as ``careful``. They are never included
in automatic cleanup categories, and their numbered list is built directly
from the scan snapshot so rankings cannot drift between display and selection.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from .models import FileSnapshot, RiskGroup, ScanResult
from .safety import PROTECTED_HOME_TREES
from .util import human_bytes

QUICK_PERSONAL_FOLDERS = (
    "Desktop",
    "Documents",
    "Downloads",
    "Videos",
    "Pictures",
    "Music",
    "Public",
    "Templates",
)


def quick_personal_roots(home: str) -> tuple[str, ...]:
    home_path = Path(home).expanduser().absolute()
    roots: list[str] = []
    for name in QUICK_PERSONAL_FOLDERS:
        folder = home_path / name
        try:
            if folder.is_dir() and not folder.is_symlink():
                roots.append(str(folder))
        except OSError:
            continue
    return tuple(roots)


def personal_excluded_paths(home: str, roots: Iterable[str] = ()) -> tuple[str, ...]:
    home_path = Path(home).expanduser().absolute()
    excluded = {
        str(home_path / ".cache"),
        str(home_path / ".local" / "share" / "Trash"),
        str(home_path / ".var"),
        str(home_path / ".config"),
        str(home_path / ".local"),
        str(home_path / ".ssh"),
        str(home_path / ".gnupg"),
        str(home_path / ".pki"),
        str(home_path / ".mozilla"),
        str(home_path / ".thunderbird"),
        str(home_path / ".steam"),
        str(home_path / ".wine"),
        str(home_path / ".android"),
    }
    for root in roots:
        # A custom scan root is allowed, but never treat its cache/Trash
        # subdirectories as personal large-file candidates.
        excluded.add(str(Path(root) / ".cache"))
    return tuple(sorted(excluded))


def discover_application_roots(home: str) -> dict[str, str]:
    """Find identifiable application data containers.

    Sizes are attributed during the main traversal, so this adds no second
    filesystem walk. Labels are intentionally descriptive, not claims about
    the contents.
    """

    home_path = Path(home).expanduser().absolute()
    mapping: dict[str, str] = {}

    flatpak_roots = (
        home_path / ".var" / "app",
        Path("/var/lib/flatpak/app"),
    )
    for base in flatpak_roots:
        _add_glob_dirs(
            mapping,
            base,
            lambda name: f"Flatpak app · {name}",
        )

    steam_common_roots = (
        home_path / ".local" / "share" / "Steam" / "steamapps" / "common",
        home_path / ".steam" / "steam" / "steamapps" / "common",
        home_path
        / ".var"
        / "app"
        / "com.valvesoftware.Steam"
        / ".local"
        / "share"
        / "Steam"
        / "steamapps"
        / "common",
    )
    for base in steam_common_roots:
        _add_glob_dirs(mapping, base, lambda name: f"Steam game · {name}")

    return mapping


def _add_glob_dirs(mapping: dict[str, str], base: Path, labeller) -> None:
    try:
        if not base.is_dir() or base.is_symlink():
            return
        for child in sorted(base.iterdir(), key=lambda p: p.name):
            try:
                if child.is_dir() and not child.is_symlink():
                    mapping[str(child.absolute())] = labeller(child.name)
            except OSError:
                continue
    except OSError:
        return


def personal_file_rows(
    result: ScanResult,
    *,
    limit: int = 50,
) -> list[FileSnapshot]:
    """Return the stable, already-ranked personal file list."""

    files = sorted(result.top_files, key=lambda f: (-f.size, f.path))
    return files[: min(limit, result.config.top_n)]


def top_file_total(result: ScanResult) -> int:
    return sum(f.size for f in result.top_files)


def describe_personal_file(file: FileSnapshot) -> str:
    return (
        f"{file.filename} · {human_bytes(file.size)} · {file.description} "
        "AppSweep did not open or read this file."
    )


def numbered_personal_map(result: ScanResult) -> dict[int, FileSnapshot]:
    """Create the exact rank-to-file mapping shown to the user.

    Numbering starts at 1 and is never re-sorted after the report is built.
    """

    return {index: file for index, file in enumerate(personal_file_rows(result), start=1)}


def all_personal_group(result: ScanResult) -> RiskGroup:
    return RiskGroup.CAREFUL


def hidden_path_parts(path: str, root: str) -> bool:
    try:
        relative = os.path.relpath(path, root)
    except ValueError:
        return True
    return any(part.startswith(".") for part in Path(relative).parts)


def protected_personal_names() -> tuple[str, ...]:
    return PROTECTED_HOME_TREES
