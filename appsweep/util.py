"""Small shared helpers with no side effects."""

from __future__ import annotations

import math
import os
import re
import time
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

SIZE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def human_bytes(size: float | None, *, precision: int | None = None) -> str:
    """Format bytes with binary units, e.g. ``1.4 GB``.

    AppSweep uses 1024-based units throughout. The label is intentionally the
    familiar GB/MB form rather than GiB/MiB, matching the rest of the utility.
    """

    if size is None:
        return "unknown"
    try:
        value = float(size)
    except (TypeError, ValueError):
        return "unknown"
    if not math.isfinite(value) or value < 0:
        value = 0.0
    index = 0
    while value >= 1024.0 and index < len(SIZE_UNITS) - 1:
        value /= 1024.0
        index += 1
    if precision is None:
        if index == 0:
            return f"{int(value)} {SIZE_UNITS[index]}"
        precision = 2 if value < 10 else 1
    return f"{value:.{precision}f} {SIZE_UNITS[index]}"


def parse_size(raw: str) -> int:
    """Parse ``512``, ``10MB``, ``1.5 GiB``, or similar into bytes."""

    text = raw.strip().replace("_", "")
    if not text:
        raise ValueError("size cannot be empty")
    match = re.fullmatch(
        r"(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>b|k|kb|kib|m|mb|mib|g|gb|gib|t|tb|tib)?",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        raise ValueError(f"invalid size: {raw!r}")
    number = float(match.group("number"))
    unit = (match.group("unit") or "b").lower()
    powers = {
        "b": 0,
        "k": 1,
        "kb": 1,
        "kib": 1,
        "m": 2,
        "mb": 2,
        "mib": 2,
        "g": 3,
        "gb": 3,
        "gib": 3,
        "t": 4,
        "tb": 4,
        "tib": 4,
    }
    return int(number * (1024 ** powers[unit]))


def human_duration(seconds: float) -> str:
    seconds = max(0, round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s" if seconds else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def timestamp_from_epoch(epoch: float | None) -> str:
    if epoch is None:
        return "unknown"
    try:
        return datetime.fromtimestamp(float(epoch)).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return "unknown"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def truncate_middle(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    left = (width - 1) // 2
    right = width - left - 1
    return text[:left] + "…" + text[-right:]


def safe_terminal_text(value: object) -> str:
    """Make untrusted names and paths safe to print in a terminal.

    File names may contain control characters, including escape sequences that
    could alter the terminal or imitate AppSweep's own prompts. Every path and
    label that comes from the filesystem is passed through this helper before
    it is displayed.
    """

    text = str(value)
    output: list[str] = []
    for character in text:
        if character == "\n":
            output.append("\\n")
        elif character == "\r":
            output.append("\\r")
        elif character == "\t":
            output.append("\\t")
        elif character.isprintable() or character == " ":
            output.append(character)
        else:
            code = ord(character)
            output.append(f"\\x{code:02x}" if code <= 0xFF else f"\\u{code:04x}")
    return "".join(output)



def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    return singular if count == 1 else (plural_form or singular + "s")


def unique_paths(paths: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in paths:
        try:
            path = str(Path(raw).expanduser().absolute())
        except (OSError, RuntimeError):
            continue
        if path not in seen:
            seen.add(path)
            result.append(path)
    return tuple(result)


def is_within(path: str, parent: str) -> bool:
    """Lexically test whether path is parent or a child of parent."""

    try:
        child = os.path.normpath(os.path.abspath(path))
        base = os.path.normpath(os.path.abspath(parent))
    except (OSError, ValueError):
        return False
    if child == base:
        return True
    return child.startswith(base.rstrip(os.sep) + os.sep)


def path_depth(path: str) -> int:
    return len(Path(path).parts)


def format_command(args: Iterable[str]) -> str:
    """Display a command safely; never execute the returned string."""

    parts: list[str] = []
    for arg in args:
        if not arg:
            parts.append("''")
            continue
        if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", arg):
            parts.append(arg)
        else:
            parts.append("'" + arg.replace("'", "'\\''") + "'")
    return " ".join(parts)


class Stopwatch:
    def __init__(self) -> None:
        self.started = time.monotonic()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started
