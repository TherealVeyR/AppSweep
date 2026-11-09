"""Scanner traversal, ranking, cancellation, symlink, and mount tests."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from appssweep.filesystem import MountInfo
from appssweep.models import ScanConfig
from appssweep.scanner import CancellationToken, StorageScanner


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
