"""Interactive selection parsing.

The numbered maps are built once from the displayed report and are never
re-sorted. Selecting an invalid number fails instead of silently choosing a
different item.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TypeVar

from .models import CleanupItem, FileSnapshot, RiskGroup

T = TypeVar("T")
GROUP_WORDS = {
    "safe": RiskGroup.SAFE,
    "careful": RiskGroup.CAREFUL,
    "rebuild": RiskGroup.REBUILD,
}
TOKEN_SPLIT = re.compile(r"[\s,]+")
RANGE_RE = re.compile(r"^(\d+)\s*-\s*(\d+)$")
NUMBER_RE = re.compile(r"^\d+$")


class SelectionError(ValueError):
    pass


@dataclass
class ParsedSelection:
    ids: set[int]
    cancelled: bool = False
    empty: bool = False
    from_all: bool = False

    def resolve(self, choices: Mapping[int, T]) -> list[T]:
        return [choices[number] for number in sorted(self.ids) if number in choices]


def parse_selection(
    text: str,
    choices: Mapping[int, T],
    *,
    eligible: Callable[[T], bool] | None = None,
    group_of: Callable[[T], RiskGroup] | None = None,
    allow_groups: bool = True,
) -> ParsedSelection:
    """Parse a selection such as ``1, 3-6, 10`` or ``all, !4``."""

    eligible = eligible or (lambda value: True)
    group_of = group_of or (lambda value: RiskGroup.CAREFUL)
    raw = (text or "").strip()
    normalized = raw.lower()
    if normalized in {"cancel", "quit", "q", "abort"}:
        return ParsedSelection(ids=set(), cancelled=True)
    if normalized in {"", "none", "no", "skip"}:
        return ParsedSelection(ids=set(), empty=True)
    if normalized in {"all", "*"}:
        ids = {
            number for number, value in choices.items() if eligible(value)
        }
        return ParsedSelection(ids=ids, from_all=True)

    tokens = [token for token in TOKEN_SPLIT.split(raw) if token]
    additions: set[int] = set()
    exclusions: set[int] = set()
    excluded_groups: set[RiskGroup] = set()
    from_all = False
    saw_positive = False

    for token in tokens:
        token_lower = token.lower()
        if token_lower in {"cancel", "quit", "q", "abort", "none", "no", "skip"}:
            raise SelectionError(
                f"'{token}' must be used by itself, not combined with other selections."
            )

        exclude = token_lower.startswith("!")
        body = token_lower[1:] if exclude else token_lower
        if not body:
            raise SelectionError("empty exclusion; use ! followed by a number or group")

        if body in {"all", "*"}:
            from_all = True
            target = {
                number for number, value in choices.items() if eligible(value)
            }
            if exclude:
                # Removing everything leaves nothing; allow explicit all exclusion.
                exclusions.update(target)
            else:
                additions.update(target)
                saw_positive = True
            continue

        if allow_groups and body in GROUP_WORDS:
            group = GROUP_WORDS[body]
            target = {
                number
                for number, value in choices.items()
                if group_of(value) == group and eligible(value)
            }
            if exclude:
                excluded_groups.add(group)
                exclusions.update(target)
            else:
                additions.update(target)
                saw_positive = True
            continue

        if NUMBER_RE.fullmatch(body):
            number = int(body)
            _validate_number(number, choices, eligible)
            if exclude:
                exclusions.add(number)
            else:
                additions.add(number)
                saw_positive = True
            continue

        range_match = RANGE_RE.fullmatch(body)
        if range_match:
            start = int(range_match.group(1))
            end = int(range_match.group(2))
            if start > end:
                raise SelectionError(f"invalid range {body}: start is greater than end")
            range_numbers = set(range(start, end + 1))
            if not range_numbers:
                raise SelectionError(f"invalid range {body}")
            for number in range_numbers:
                _validate_number(number, choices, eligible)
            if exclude:
                exclusions.update(range_numbers)
            else:
                additions.update(range_numbers)
                saw_positive = True
            continue

        raise SelectionError(
            f"unrecognized selection '{token}'. Use numbers, ranges like 1-5, "
            "group words (safe/careful/rebuild), all, none, cancel, or ! to exclude."
        )

    if not saw_positive and not exclusions and not from_all:
        raise SelectionError("no items were selected")
    selected = additions - exclusions
    # Selecting "all" then excluding a whole group should not accidentally
    # re-add excluded numbers.
    if excluded_groups:
        selected = {
            number
            for number in selected
            if group_of(choices[number]) not in excluded_groups
        }
    return ParsedSelection(ids=selected, from_all=from_all, empty=not selected)


def _validate_number(
    number: int,
    choices: Mapping[int, T],
    eligible: Callable[[T], bool],
) -> None:
    if number not in choices:
        if not choices:
            raise SelectionError("there are no selectable items")
        raise SelectionError(
            f"item {number} is not in this report (valid range: "
            f"{min(choices)}-{max(choices)})"
        )
    if not eligible(choices[number]):
        raise SelectionError(
            f"item {number} is report-only or disabled and cannot be selected"
        )


def parse_file_selection(
    text: str, files: Mapping[int, FileSnapshot]
) -> ParsedSelection:
    return parse_selection(
        text,
        files,
        eligible=lambda file: True,
        group_of=lambda file: RiskGroup.CAREFUL,
        allow_groups=False,
    )


def parse_cleanup_selection(
    text: str, items: Mapping[int, CleanupItem]
) -> ParsedSelection:
    return parse_selection(
        text,
        items,
        eligible=lambda item: item.eligible,
        group_of=lambda item: item.group,
        allow_groups=True,
    )


def selection_preview(
    numbers: Iterable[int],
    choices: Mapping[int, T],
    label: Callable[[T], str],
) -> list[str]:
    return [
        f"{number:>3}. {label(choices[number])}"
        for number in sorted(numbers)
        if number in choices
    ]


def format_number_set(numbers: Iterable[int]) -> str:
    numbers = sorted(set(numbers))
    if not numbers:
        return "none"
    ranges: list[str] = []
    start = previous = numbers[0]
    for number in numbers[1:]:
        if number == previous + 1:
            previous = number
            continue
        ranges.append(_format_range(start, previous))
        start = previous = number
    ranges.append(_format_range(start, previous))
    return ", ".join(ranges)


def _format_range(start: int, end: int) -> str:
    return str(start) if start == end else f"{start}-{end}"
