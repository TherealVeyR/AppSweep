"""Filesystem traversal, measurement, mount, and boundary tests."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from appssweep.filesystem import (
    MountInfo,
    discover_installation_roots,
    is_mount_boundary,
    is_within,
    measure_path,
    read_mountinfo,
)
from appssweep.util import is_within as util_is_within
from appssweep.util import safe_terminal_text


class FilesystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_measure_path_counts_regular_files_and_hardlinks_once(self) -> None:
        (self.root / "a.bin").write_bytes(b"a" * 1000)
        (self.root / "b.bin").write_bytes(b"b" * 2000)
        (self.root / "sub").mkdir()
        (self.root / "sub" / "c.bin").write_bytes(b"c" * 3000)
        os.link(self.root / "a.bin", self.root / "a-hardlink.bin")
        os.symlink(self.root / "a.bin", self.root / "link.bin")

        result = measure_path(str(self.root))

        self.assertEqual(result.logical_size, 6000)
        self.assertEqual(result.files, 3)
        self.assertEqual(result.symlinks, 1)
        self.assertFalse(result.incomplete)
        self.assertEqual(result.permission_errors, [])

    def test_measure_path_honours_excludes(self) -> None:
        (self.root / "keep.bin").write_bytes(b"k" * 512)
        (self.root / "skip").mkdir()
        (self.root / "skip" / "big.bin").write_bytes(b"b" * 8192)

        result = measure_path(
            str(self.root), excluded_paths=(str(self.root / "skip"),)
        )

        self.assertEqual(result.logical_size, 512)

    def test_measure_path_does_not_follow_root_symlink(self) -> None:
        target = self.root / "target"
        target.mkdir()
        (target / "file.bin").write_bytes(b"x" * 1024)
        link = self.root / "link"
        os.symlink(target, link)

        result = measure_path(str(link))

        self.assertTrue(result.incomplete)
        self.assertEqual(result.logical_size, 0)
        self.assertEqual(result.symlinks, 1)
        self.assertTrue(result.permission_errors)

    def test_measure_path_does_not_follow_inside_symlink(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.bin").write_bytes(b"s" * 4096)
        scan = self.root / "scan"
        scan.mkdir()
        (scan / "inside.bin").write_bytes(b"i" * 128)
        os.symlink(outside, scan / "outside-link")

        result = measure_path(str(scan))

        self.assertEqual(result.logical_size, 128)
        self.assertEqual(result.symlinks, 1)

    def test_measure_path_skips_same_device_nested_mount(self) -> None:
        mounted = self.root / "mounted"
        mounted.mkdir()
        (mounted / "inside.bin").write_bytes(b"x" * 1024)
        mounts = [
            MountInfo(
                mount_point=str(mounted),
                source="/dev/same",
                fstype="ext4",
                options="rw",
                major_minor="0:42",
            )
        ]

        result = measure_path(str(self.root), mounts=mounts)

        self.assertEqual(result.logical_size, 0)
        self.assertTrue(
            any(skip.path == str(mounted) for skip in result.skipped)
        )
        self.assertTrue(
            any("nested mount boundary" in skip.reason for skip in result.skipped)
        )

    def test_measure_path_records_permission_error(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root can read every test directory")
        blocked = self.root / "blocked"
        blocked.mkdir()
        (blocked / "file.bin").write_bytes(b"x" * 100)
        os.chmod(blocked, 0)
        self.addCleanup(
            lambda: os.chmod(blocked, stat.S_IRWXU) if blocked.exists() else None
        )

        result = measure_path(str(self.root))

        self.assertTrue(result.incomplete)
        self.assertTrue(result.permission_errors)
        self.assertEqual(result.logical_size, 0)

    def test_is_within_is_lexical_and_boundary_aware(self) -> None:
        self.assertTrue(is_within("/home/user/Documents/a", "/home/user"))
        self.assertTrue(is_within("/home/user", "/home/user"))
        self.assertFalse(is_within("/home/user2/a", "/home/user"))
        self.assertEqual(is_within, util_is_within)

    def test_read_mountinfo_decodes_escaped_mount_points(self) -> None:
        mountinfo = self.root / "mountinfo"
        mountinfo.write_text(
            "36 25 0:32 / / rw,relatime - btrfs /dev/sda1 rw\n"
            "37 36 0:33 / /mnt/My\\040Disk rw - ext4 /dev/sdb1 rw\n",
            encoding="utf-8",
        )
        mounts = read_mountinfo(str(mountinfo))
        self.assertEqual(len(mounts), 2)
        self.assertEqual(mounts[1].mount_point, "/mnt/My Disk")
        self.assertEqual(mounts[1].fstype, "ext4")
        self.assertEqual(mounts[1].source, "/dev/sdb1")

    def test_mount_boundary_reports_virtual_and_network_mounts(self) -> None:
        root_dev = os.stat(self.root).st_dev
        virtual_dir = self.root / "virtual"
        virtual_dir.mkdir()
        mounts = {
            str(virtual_dir): MountInfo(
                mount_point=str(virtual_dir), source="proc", fstype="proc"
            )
        }
        boundary, reason = is_mount_boundary(str(virtual_dir), root_dev, mounts)
        self.assertTrue(boundary)
        self.assertIn("proc", reason)

    def test_mount_boundary_reports_same_device_local_mount(self) -> None:
        root_dev = os.stat(self.root).st_dev
        local_dir = self.root / "local-mount"
        local_dir.mkdir()
        mounts = {
            str(local_dir): MountInfo(
                mount_point=str(local_dir),
                source="/dev/same",
                fstype="ext4",
                major_minor="0:42",
            )
        }

        boundary, reason = is_mount_boundary(str(local_dir), root_dev, mounts)

        self.assertTrue(boundary)
        self.assertIn("mount boundary", reason)

    def test_installation_roots_flatpak_sandbox(self) -> None:
        roots, notes, _mounts = discover_installation_roots(
            str(self.root), sandboxed=True
        )
        self.assertEqual(roots, [str(self.root.absolute())])
        self.assertTrue(any("Flatpak sandbox" in note for note in notes))

    def test_installation_roots_include_separate_system_mounts(self) -> None:
        mounts = [
            MountInfo("/", "/dev/root", "btrfs"),
            MountInfo("/var", "/dev/var", "ext4"),
            MountInfo("/usr", "composefs", "composefs"),
            MountInfo("/boot", "/dev/boot", "vfat"),
            MountInfo("/opt", "/dev/opt", "ext4"),
            MountInfo("/etc", "/dev/etc", "ext4"),
            MountInfo("/root", "/dev/root-home", "ext4"),
            MountInfo("/srv", "/dev/srv", "ext4"),
            MountInfo("/tmp", "/dev/tmp", "ext4"),
        ]
        devices = {
            "/": 1,
            "/var": 2,
            "/usr": 3,
            "/boot": 4,
            "/opt": 5,
            "/etc": 6,
            "/root": 7,
            "/srv": 8,
            "/tmp": 9,
        }
        existing = set(devices)

        def fake_isdir(path: str) -> bool:
            return path in existing

        def fake_stat(path: str) -> SimpleNamespace:
            return SimpleNamespace(st_dev=devices[path])

        with (
            patch("appssweep.filesystem.os.path.isdir", side_effect=fake_isdir),
            patch("appssweep.filesystem.os.stat", side_effect=fake_stat),
        ):
            roots, notes, _mounts = discover_installation_roots(
                "/home/test", sandboxed=False, mounts=mounts
            )

        self.assertEqual(set(roots), set(devices))
        self.assertTrue(any("read-only composefs image" in note for note in notes))
        self.assertTrue(any("/opt is a separate local ext4" in note for note in notes))
        self.assertTrue(any("/tmp is a separate local ext4" in note for note in notes))

    def test_installation_roots_skip_separate_tmpfs_tmp(self) -> None:
        mounts = [
            MountInfo("/", "/dev/root", "btrfs"),
            MountInfo("/tmp", "tmpfs", "tmpfs"),
        ]
        devices = {"/": 1, "/tmp": 2}
        existing = {"/", "/tmp"}

        with (
            patch(
                "appssweep.filesystem.os.path.isdir",
                side_effect=lambda path: path in existing,
            ),
            patch(
                "appssweep.filesystem.os.stat",
                side_effect=lambda path: SimpleNamespace(st_dev=devices[path]),
            ),
        ):
            roots, notes, _mounts = discover_installation_roots(
                "/home/test", sandboxed=False, mounts=mounts
            )

        self.assertNotIn("/tmp", roots)
        self.assertTrue(any("/tmp is tmpfs; skipped" in note for note in notes))

    def test_safe_terminal_text_escapes_control_characters(self) -> None:
        rendered = safe_terminal_text("a\x1b[31mred\nline\twith tab")
        self.assertNotIn("\x1b", rendered)
        self.assertIn("\\x1b", rendered)
        self.assertIn("\\n", rendered)
        self.assertIn("\\t", rendered)


if __name__ == "__main__":
    unittest.main()
