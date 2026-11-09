"""Tests for numbered selection parsing and display mapping."""

from __future__ import annotations

import unittest

from appssweep.models import CleanupItem, RiskGroup
from appssweep.selection import (
    SelectionError,
    format_number_set,
    parse_cleanup_selection,
    parse_file_selection,
)


def cleanup(number: int, group: RiskGroup, eligible: bool = True) -> CleanupItem:
    return CleanupItem(
        key=f"item-{number}",
        title=f"Item {number}",
        group=group,
        size_bytes=number * 100,
        summary="summary",
        description="description",
        consequences="consequences",
        method="method",
        action=__import__("appssweep.models", fromlist=["ActionKind"]).ActionKind.REPORT_ONLY,
        eligible=eligible,
        number=number,
    )


class SelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.files = {index: f"file-{index}" for index in range(1, 11)}
        self.items = {
            1: cleanup(1, RiskGroup.SAFE),
            2: cleanup(2, RiskGroup.SAFE),
            3: cleanup(3, RiskGroup.CAREFUL),
            4: cleanup(4, RiskGroup.CAREFUL),
            5: cleanup(5, RiskGroup.REBUILD),
            6: cleanup(6, RiskGroup.REBUILD, eligible=False),
        }

    def test_single_number(self) -> None:
        self.assertEqual(parse_file_selection("1", self.files).ids, {1})

    def test_comma_list(self) -> None:
        self.assertEqual(parse_file_selection("1,3,5", self.files).ids, {1, 3, 5})

    def test_range_and_mixed(self) -> None:
        parsed = parse_file_selection("1-3, 8, 10", self.files)
        self.assertEqual(parsed.ids, {1, 2, 3, 8, 10})

    def test_all_and_none(self) -> None:
        self.assertEqual(parse_file_selection("all", self.files).ids, set(range(1, 11)))
        parsed = parse_file_selection("none", self.files)
        self.assertTrue(parsed.empty)
        self.assertEqual(parsed.ids, set())

    def test_exclusion(self) -> None:
        parsed = parse_file_selection("all, !4, !8-9", self.files)
        self.assertEqual(parsed.ids, {1, 2, 3, 5, 6, 7, 10})

    def test_group_words(self) -> None:
        parsed = parse_cleanup_selection("safe", self.items)
        self.assertEqual(parsed.ids, {1, 2})
        parsed = parse_cleanup_selection("all, !rebuild", self.items)
        self.assertEqual(parsed.ids, {1, 2, 3, 4})

    def test_group_words_are_not_file_selection(self) -> None:
        with self.assertRaises(SelectionError):
            parse_file_selection("safe", self.files)

    def test_report_only_item_rejected(self) -> None:
        with self.assertRaises(SelectionError) as context:
            parse_cleanup_selection("6", self.items)
        self.assertIn("report-only", str(context.exception))

    def test_invalid_number_rejected(self) -> None:
        with self.assertRaises(SelectionError) as context:
            parse_file_selection("99", self.files)
        self.assertIn("valid range", str(context.exception))

    def test_reversed_range_rejected(self) -> None:
        with self.assertRaises(SelectionError):
            parse_file_selection("8-3", self.files)

    def test_huge_range_rejected_before_materializing(self) -> None:
        with self.assertRaises(SelectionError) as context:
            parse_file_selection("1-100000000000000000000", self.files)
        self.assertIn("valid range", str(context.exception))
        with self.assertRaises(SelectionError):
            parse_file_selection("all, !1-100000000000000000000", self.files)

    def test_range_cannot_exceed_available_choice_count(self) -> None:
        sparse = {1: "one", 10: "ten"}
        with self.assertRaises(SelectionError) as context:
            parse_file_selection("1-10", sparse)
        self.assertIn("selectable items", str(context.exception))

    def test_absurdly_long_number_is_rejected_as_selection_error(self) -> None:
        with self.assertRaises(SelectionError):
            parse_file_selection("9" * 5000, self.files)

    def test_garbage_rejected(self) -> None:
        with self.assertRaises(SelectionError):
            parse_file_selection("largest", self.files)

    def test_cancel_and_none_cannot_be_combined(self) -> None:
        with self.assertRaises(SelectionError):
            parse_file_selection("1, none", self.files)

    def test_cancel(self) -> None:
        self.assertTrue(parse_file_selection("cancel", self.files).cancelled)
        self.assertTrue(parse_file_selection("quit", self.files).cancelled)

    def test_empty_and_no_items(self) -> None:
        with self.assertRaises(SelectionError):
            parse_file_selection("1", {})
        self.assertTrue(parse_file_selection("", self.files).empty)

    def test_format_number_set(self) -> None:
        self.assertEqual(format_number_set([1, 2, 3, 5, 7, 8]), "1-3, 5, 7-8")
        self.assertEqual(format_number_set([]), "none")


if __name__ == "__main__":
    unittest.main()
