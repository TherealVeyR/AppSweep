"""Scanner traversal, ranking, cancellation, symlink, and mount tests."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from appssweep.filesystem import MountInfo
from appssweep.models import ScanConfig
from appssweep.scanner import CancellationToken, StorageScanner
from appssweep.scanner import scan as convenience_scan


def config(root: Path, **overrides) -> ScanConfig:
    values = {
        "mode": "custom",
        "roots": (str(root),),
        "min_file_size": 0,
        "top_n": 50,
        "personal_roots": (str(root),),
        "personal_excluded_paths": (),
        "excluded_paths": (),
        "detect_mounts": False,
    }
    values.update(overrides)
    return ScanConfig(**values).normalized()


class ScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.mounts = [
            MountInfo(
                mount_point=str(self.root),
                source="tmpfs",
                fstype="tmpfs",
                options="rw",
            )
        ]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def scan(self, scan_config: ScanConfig, **kwargs):
        return StorageScanner(
            scan_config,
            mounts=kwargs.pop("mounts", self.mounts),
            **kwargs,
        ).scan()

    def test_ranks_largest_files_and_respects_top_n(self) -> None:
        for size in range(1, 61):
            (self.root / f"file-{size:02d}.bin").write_bytes(b"x" * size)

        result = self.scan(config(self.root, top_n=50))

        self.assertEqual(len(result.top_files), 50)
        sizes = [file.size for file in result.top_files]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertEqual(sizes[0], 60)
        self.assertEqual(sizes[-1], 11)
        self.assertNotIn(10, sizes)

    def test_symlinks_are_counted_but_not_followed_or_ranked(self) -> None:
        target = self.root / "target.bin"
        target.write_bytes(b"x" * 4096)
        os.symlink(target, self.root / "link.bin")

        result = self.scan(config(self.root))

        self.assertEqual(result.total_symlinks, 1)
        self.assertEqual(result.unique_files, 1)
        self.assertEqual([file.filename for file in result.top_files], ["target.bin"])

    def test_hardlinks_are_counted_once_for_storage_and_ranking(self) -> None:
        large = self.root / "large.bin"
        large.write_bytes(b"x" * 8192)
        os.link(large, self.root / "hardlink.bin")
        (self.root / "small.bin").write_bytes(b"y" * 1024)

        result = self.scan(config(self.root))

        self.assertEqual(result.total_files, 3)
        self.assertEqual(result.unique_files, 2)
        self.assertEqual(result.total_bytes, 8192 + 1024)
        self.assertEqual(len(result.top_files), 2)

    def test_hardlinks_across_scan_roots_are_counted_once_globally(self) -> None:
        first = self.root / "first"
        second = self.root / "second"
        first.mkdir()
        second.mkdir()
        original = first / "shared.bin"
        original.write_bytes(b"x" * 4096)
        linked = second / "shared.bin"
        os.link(original, linked)
        expected_allocated = max(
            0, int(getattr(os.lstat(original), "st_blocks", 0)) * 512
        )
        scan_config = config(
            self.root,
            roots=(str(first), str(second)),
            personal_roots=(str(first), str(second)),
        )

        result = self.scan(scan_config)

        self.assertEqual(result.total_files, 2)
        self.assertEqual(result.unique_files, 1)
        self.assertEqual(result.total_bytes, 4096)
        self.assertEqual(result.total_allocated, expected_allocated)
        self.assertEqual(sum(result.file_type_sizes.values()), 4096)
        self.assertEqual(len(result.top_files), 1)
        self.assertEqual(result.top_files[0].st_ino, os.lstat(original).st_ino)

    def test_hardlinked_loose_file_roots_are_counted_once_globally(self) -> None:
        first = self.root / "loose-one.bin"
        second = self.root / "loose-two.bin"
        first.write_bytes(b"x" * 2048)
        os.link(first, second)
        scan_config = config(
            self.root,
            roots=(str(first), str(second)),
            personal_roots=(str(self.root),),
        )

        result = self.scan(scan_config)

        self.assertEqual(result.total_files, 2)
        self.assertEqual(result.unique_files, 1)
        self.assertEqual(result.total_bytes, 2048)

    def test_same_basename_in_separate_roots_is_counted_independently(self) -> None:
        first = self.root / "first"
        second = self.root / "second"
        first.mkdir()
        second.mkdir()
        (first / "report.bin").write_bytes(b"a" * 1000)
        (second / "report.bin").write_bytes(b"b" * 2000)
        scan_config = config(
            self.root,
            roots=(str(first), str(second)),
            personal_roots=(str(first), str(second)),
        )

        result = self.scan(scan_config)

        self.assertEqual(result.total_files, 2)
        self.assertEqual(result.unique_files, 2)
        self.assertEqual(result.total_bytes, 3000)
        self.assertEqual(len(result.top_files), 2)

    def test_hidden_files_are_not_personal_candidates(self) -> None:
        (self.root / "visible.bin").write_bytes(b"v" * 1000)
        hidden_dir = self.root / ".hidden"
        hidden_dir.mkdir()
        (hidden_dir / "secret.bin").write_bytes(b"s" * 5000)

        result = self.scan(config(self.root))

        self.assertEqual([file.filename for file in result.top_files], ["visible.bin"])
        self.assertEqual(result.total_bytes, 6000)

    def test_cache_and_trash_paths_are_not_personal_candidates(self) -> None:
        (self.root / "plain.bin").write_bytes(b"p" * 1000)
        cache_dir = self.root / ".cache" / "app"
        cache_dir.mkdir(parents=True)
        (cache_dir / "cache.bin").write_bytes(b"c" * 5000)
        trash_dir = self.root / ".local" / "share" / "Trash" / "files"
        trash_dir.mkdir(parents=True)
        (trash_dir / "old.bin").write_bytes(b"t" * 5000)

        result = self.scan(config(self.root))

        self.assertEqual([file.filename for file in result.top_files], ["plain.bin"])

    def test_cancellation_returns_partial_result_and_marks_cancelled(self) -> None:
        (self.root / "file.bin").write_bytes(b"x" * 1000)
        token = CancellationToken()
        token.cancel()

        result = self.scan(config(self.root), cancel_token=token)

        self.assertTrue(result.cancelled)
        self.assertTrue(result.coverage_notes)
        self.assertEqual(result.top_files, [])

    def test_cancellation_mid_scan(self) -> None:
        for index in range(100):
            (self.root / f"file-{index}.bin").write_bytes(b"x" * index)
        token = CancellationToken()
        call_count = {"count": 0}

        def progress(*_args) -> None:
            call_count["count"] += 1
            if call_count["count"] >= 1:
                token.cancel()

        result = self.scan(config(self.root), cancel_token=token, progress=progress)

        self.assertTrue(result.cancelled)

    def test_convenience_cancel_check_is_invoked_immediately(self) -> None:
        (self.root / "file.bin").write_bytes(b"x" * 1024)
        calls = {"count": 0}

        def cancel_check() -> bool:
            calls["count"] += 1
            return True

        result = convenience_scan(config(self.root), cancel_check=cancel_check)

        self.assertGreaterEqual(calls["count"], 1)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.total_files, 0)
        self.assertTrue(
            any("cancelled" in note.lower() for note in result.coverage_notes)
        )

    def test_convenience_cancel_check_can_cancel_mid_scan(self) -> None:
        for index in range(4):
            directory = self.root / f"dir-{index}"
            directory.mkdir()
            (directory / "file.bin").write_bytes(b"x" * 1024)
        calls = {"count": 0}

        def cancel_check() -> bool:
            calls["count"] += 1
            return calls["count"] >= 4

        result = convenience_scan(config(self.root), cancel_check=cancel_check)

        self.assertGreaterEqual(calls["count"], 4)
        self.assertTrue(result.cancelled)
        self.assertLess(result.total_files, 4)

    def test_permission_errors_are_reported(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root can read every test directory")
        blocked = self.root / "blocked"
        blocked.mkdir()
        (blocked / "inside.bin").write_bytes(b"x" * 100)
        os.chmod(blocked, 0)
        self.addCleanup(
            lambda: os.chmod(blocked, stat.S_IRWXU) if blocked.exists() else None
        )

        result = self.scan(config(self.root))

        self.assertGreaterEqual(result.permission_error_count, 1)
        self.assertTrue(result.permission_issues)

    def test_root_symlink_is_reported_and_not_followed(self) -> None:
        real = self.root / "real"
        real.mkdir()
        (real / "secret.bin").write_bytes(b"x" * 1024)
        link = self.root / "root-link"
        os.symlink(real, link)

        result = self.scan(config(link))

        self.assertEqual(result.total_files, 0)
        self.assertTrue(any("symbolic link" in note for note in result.coverage_notes))

    @unittest.skipIf(
        os.geteuid() == 0, "deterministic race tests require an unprivileged user"
    )
    def test_directory_swap_at_enumeration_does_not_traverse_outside(self) -> None:
        queued = self.root / "queued"
        queued.mkdir()
        (queued / "inside.bin").write_bytes(b"i" * 128)
        outside_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(outside_tmp.cleanup)
        outside = Path(outside_tmp.name)
        victim = outside / "keep-me.txt"
        victim.write_bytes(b"o" * 23)
        moved = self.root / "queued-original"
        real_scandir = os.scandir
        calls = {"count": 0}

        def swap_on_second_scandir(target):
            calls["count"] += 1
            if calls["count"] == 2:
                os.rename(queued, moved)
                os.symlink(outside, queued)
            return real_scandir(target)

        with patch("appssweep.scanner.os.scandir", side_effect=swap_on_second_scandir):
            result = self.scan(config(self.root))

        self.assertGreaterEqual(calls["count"], 2)
        self.assertEqual(victim.read_bytes(), b"o" * 23)
        self.assertEqual(result.total_files, 1)
        self.assertEqual([item.filename for item in result.top_files], ["inside.bin"])

    @unittest.skipIf(
        os.geteuid() == 0, "deterministic race tests require an unprivileged user"
    )
    def test_directory_swap_before_open_is_skipped(self) -> None:
        queued = self.root / "queued-before-open"
        queued.mkdir()
        (queued / "inside.bin").write_bytes(b"i" * 128)
        outside_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(outside_tmp.cleanup)
        outside = Path(outside_tmp.name)
        victim = outside / "keep-me.txt"
        victim.write_bytes(b"o" * 23)
        moved = self.root / "queued-before-open-original"
        real_open = os.open
        swapped = False

        def swap_before_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if not swapped and os.fspath(path) == str(queued):
                swapped = True
                os.rename(queued, moved)
                os.symlink(outside, queued)
            return real_open(path, flags, *args, **kwargs)

        with patch("appssweep.scanner.os.open", side_effect=swap_before_open):
            result = self.scan(config(self.root))

        self.assertTrue(swapped)
        self.assertEqual(victim.read_bytes(), b"o" * 23)
        self.assertEqual(result.total_files, 0)
        self.assertTrue(
            any(skip.path == str(queued) for skip in result.skipped_paths)
        )

    def test_wide_directory_cancellation_does_not_materialize_every_entry(self) -> None:
        for index in range(2000):
            (self.root / f"file-{index:04d}.bin").write_bytes(b"x")
        real_scandir = os.scandir
        yielded = {"count": 0}

        class CountingScandir:
            def __init__(self, target) -> None:
                self._iterator = real_scandir(target)

            def __enter__(self):
                return self

            def __iter__(self):
                return self

            def __next__(self):
                entry = next(self._iterator)
                yielded["count"] += 1
                return entry

            def __exit__(self, exc_type, exc, traceback) -> None:
                self._iterator.close()

        def counting_scandir(target):
            return CountingScandir(target)

        calls = {"count": 0}

        def cancel_check() -> bool:
            calls["count"] += 1
            return calls["count"] >= 3

        with patch("appssweep.scanner.os.scandir", side_effect=counting_scandir):
            result = convenience_scan(config(self.root), cancel_check=cancel_check)

        self.assertTrue(result.cancelled)
        self.assertGreaterEqual(calls["count"], 3)
        self.assertLess(yielded["count"], 2000)

    def test_virtual_mount_is_skipped(self) -> None:
        mounted = self.root / "proc-mount"
        mounted.mkdir()
        (mounted / "fake.bin").write_bytes(b"x" * 1024)
        mounts = self.mounts + [
            MountInfo(
                mount_point=str(mounted),
                source="proc",
                fstype="proc",
                options="rw",
            )
        ]

        result = self.scan(config(self.root), mounts=mounts)

        self.assertEqual(result.total_files, 0)
        self.assertTrue(
            any(skip.path == str(mounted) for skip in result.skipped_paths)
        )

    def test_same_device_nested_mount_is_skipped(self) -> None:
        mounted = self.root / "same-device-mount"
        mounted.mkdir()
        (mounted / "fake.bin").write_bytes(b"x" * 1024)
        mounts = self.mounts + [
            MountInfo(
                mount_point=str(mounted),
                source="/dev/same",
                fstype="ext4",
                options="rw",
                major_minor="0:42",
            )
        ]

        result = self.scan(config(self.root), mounts=mounts)

        self.assertEqual(result.total_files, 0)
        self.assertTrue(
            any(skip.path == str(mounted) for skip in result.skipped_paths)
        )
        self.assertTrue(
            any(
                "nested mount boundary" in skip.reason
                for skip in result.skipped_paths
            )
        )

    def test_explicit_nested_mount_root_is_still_scanned(self) -> None:
        mounted = self.root / "explicit-mount-root"
        mounted.mkdir()
        (mounted / "inside.bin").write_bytes(b"x" * 1024)
        mounts = self.mounts + [
            MountInfo(
                mount_point=str(mounted),
                source="/dev/same",
                fstype="ext4",
                options="rw",
                major_minor="0:42",
            )
        ]
        scan_config = config(
            self.root,
            roots=(str(mounted),),
            personal_roots=(str(mounted),),
        )

        result = self.scan(scan_config, mounts=mounts)

        self.assertEqual(result.total_files, 1)
        self.assertFalse(result.cancelled)

    def test_top_directories_include_nested_sizes(self) -> None:
        (self.root / "a").mkdir()
        (self.root / "a" / "one.bin").write_bytes(b"x" * 1000)
        (self.root / "a" / "b").mkdir()
        (self.root / "a" / "b" / "two.bin").write_bytes(b"y" * 2000)

        result = self.scan(config(self.root))

        by_path = {os.path.normpath(directory.path): directory.size for directory in result.top_dirs}
        self.assertEqual(by_path[os.path.normpath(str(self.root))], 3000)
        sizes = {Path(directory.path).name: directory.size for directory in result.top_dirs}
        self.assertEqual(sizes["a"], 3000)
        self.assertEqual(sizes["b"], 2000)


if __name__ == "__main__":
    unittest.main()
