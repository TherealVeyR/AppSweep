"""Safety policy, snapshot revalidation, cache clearing, and authorization tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from appssweep.cleanup_engine import CleanupEngine
from appssweep.models import (
    ActionKind,
    Authorization,
    CleanupItem,
    CleanupPlan,
    RiskGroup,
)
from appssweep.safety import (
    SafetyError,
    SafetyPolicy,
    authorization_covers,
    make_authorization,
)
from appssweep.util import is_within

from .helpers import snapshot_for, write_file


class SafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        (self.home / "Documents").mkdir()
        self.cache = self.home / ".cache" / "app"
        self.cache.mkdir(parents=True)
        self.env_patch = patch.dict(
            os.environ,
            {
                "XDG_CACHE_HOME": str(self.home / ".cache"),
                "XDG_DATA_HOME": str(self.home / ".local" / "share"),
            },
        )
        self.env_patch.start()
        self.policy = SafetyPolicy(
            home=str(self.home),
            personal_roots=(str(self.home / "Documents"),),
            cache_roots=(str(self.cache),),
        )

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.tmp.cleanup()

    def test_protected_paths(self) -> None:
        self.assertTrue(self.policy.is_protected(str(self.home))[0])
        self.assertTrue(self.policy.is_protected("/etc/passwd")[0])
        self.assertTrue(self.policy.is_protected(str(self.home / ".ssh" / "key"))[0])
        self.assertFalse(self.policy.is_protected(str(self.home / "Documents" / "file"))[0])

    def test_snapshot_validation_accepts_unchanged_personal_file(self) -> None:
        file = write_file(self.home / "Documents" / "movie.mp4", 1024)
        validation = self.policy.validate_personal_snapshot(snapshot_for(file))
        self.assertTrue(validation.ok, validation.reason)

    def test_snapshot_validation_rejects_changed_file(self) -> None:
        file = write_file(self.home / "Documents" / "movie.mp4", 1024)
        snapshot = snapshot_for(file)
        file.write_bytes(b"changed-size")
        validation = self.policy.validate_personal_snapshot(snapshot)
        self.assertFalse(validation.ok)
        self.assertIn("changed", validation.reason)

    def test_snapshot_validation_rejects_hidden_and_protected_paths(self) -> None:
        hidden = write_file(self.home / "Documents" / ".hidden", 1024)
        self.assertFalse(self.policy.validate_personal_snapshot(snapshot_for(hidden)).ok)
        ssh = write_file(self.home / "Documents" / "key", 1024)
        self.policy.protected_home_names = (".ssh",)
        # A file named key is not hidden; this checks the protected-tree rule
        # by moving it under a protected directory instead.
        (self.home / ".ssh").mkdir(exist_ok=True)
        moved = self.home / ".ssh" / "key"
        os.replace(ssh, moved)
        self.assertFalse(self.policy.validate_personal_snapshot(snapshot_for(moved)).ok)

    def test_snapshot_validation_rejects_symlinked_parent(self) -> None:
        real = self.home / "Documents" / "real"
        real.mkdir()
        file = write_file(real / "file.bin", 1024)
        link_dir = self.home / "Documents" / "linked"
        os.symlink(real, link_dir)
        linked_file = link_dir / "file.bin"
        snapshot = snapshot_for(file)
        snapshot.path = str(linked_file)
        validation = self.policy.validate_personal_snapshot(snapshot)
        self.assertFalse(validation.ok)
        self.assertIn("symbolic link", validation.reason)

    def test_snapshot_validation_rejects_cache_and_trash(self) -> None:
        cache_file = write_file(self.cache / "cache.bin", 1024)
        self.assertFalse(
            self.policy.validate_personal_snapshot(snapshot_for(cache_file)).ok
        )
        trash_file = write_file(
            self.home / ".local" / "share" / "Trash" / "files" / "old.bin", 1024
        )
        self.assertFalse(
            self.policy.validate_personal_snapshot(snapshot_for(trash_file)).ok
        )

    def test_clear_cache_contents_keeps_folder_and_does_not_follow_symlink(self) -> None:
        inside = write_file(self.cache / "inside.bin", 512)
        sub = self.cache / "sub"
        sub.mkdir()
        sub_file = write_file(sub / "nested.bin", 256)
        outside = self.home / "outside.bin"
        outside.write_bytes(b"o" * 4096)
        link = self.cache / "escape"
        os.symlink(outside, link)

        result = self.policy.clear_cache_target(str(self.cache))

        self.assertFalse(inside.exists())
        self.assertFalse(sub_file.exists())
        self.assertFalse(link.exists())
        self.assertTrue(self.cache.is_dir())
        self.assertTrue(outside.exists())
        self.assertGreater(result.bytes_removed, 0)
        self.assertEqual(result.errors, [])

    def test_clear_cache_target_rejects_top_level_cache_folder(self) -> None:
        validation = self.policy.validate_cache_target(str(self.home / ".cache"))
        self.assertFalse(validation.ok)
        self.assertIn("top-level", validation.reason)

    def test_cache_override_cannot_reach_sensitive_home_trees(self) -> None:
        ssh_cache = self.home / ".ssh" / "cache"
        target = ssh_cache / "app"
        target.mkdir(parents=True)
        policy = SafetyPolicy(
            home=str(self.home),
            personal_roots=(str(self.home / "Documents"),),
            cache_roots=(str(ssh_cache),),
        )
        validation = policy.validate_cache_target(str(target))
        self.assertFalse(validation.ok)
        self.assertIn(".ssh", validation.reason)

    def test_flatpak_var_cache_override_is_allowed(self) -> None:
        var_cache = self.home / ".var" / "app" / "org.example.App" / "cache"
        target = var_cache / "app"
        target.mkdir(parents=True)
        policy = SafetyPolicy(
            home=str(self.home),
            personal_roots=(str(self.home / "Documents"),),
            cache_roots=(str(var_cache),),
        )
        validation = policy.validate_cache_target(str(target))
        self.assertTrue(validation.ok, validation.reason)

    def test_flatpak_var_override_does_not_cover_app_data(self) -> None:
        app_data = self.home / ".var" / "app" / "org.example.App" / "data"
        target = app_data / "app"
        target.mkdir(parents=True)
        policy = SafetyPolicy(
            home=str(self.home),
            personal_roots=(str(self.home / "Documents"),),
            cache_roots=(str(app_data),),
        )
        validation = policy.validate_cache_target(str(target))
        self.assertFalse(validation.ok)
        self.assertIn(".var", validation.reason)

    def test_clear_cache_target_rejects_symlink_target(self) -> None:
        target = self.home / "Documents" / "not-cache"
        target.mkdir()
        link = self.cache / "linked"
        os.symlink(target, link)
        validation = self.policy.validate_cache_target(str(link))
        self.assertFalse(validation.ok)
        self.assertIn("symbolic link", validation.reason)

    def test_remove_personal_file_revalidates_and_unlinks(self) -> None:
        file = write_file(self.home / "Documents" / "old.iso", 2048)
        snapshot = snapshot_for(file)
        result = self.policy.remove_personal_file(snapshot)
        self.assertFalse(file.exists())
        self.assertEqual(result.bytes_removed, 2048)

    def test_remove_personal_file_refuses_changed_file(self) -> None:
        file = write_file(self.home / "Documents" / "old.iso", 2048)
        snapshot = snapshot_for(file)
        file.write_bytes(b"new content")
        with self.assertRaises(SafetyError):
            self.policy.remove_personal_file(snapshot)
        self.assertTrue(file.exists())

    @unittest.skipIf(
        os.geteuid() == 0, "deterministic race tests require an unprivileged user"
    )
    def test_clear_cache_target_remains_anchored_if_directory_is_swapped(self) -> None:
        inside = write_file(self.cache / "inside.bin", 512)
        outside = self.home / "outside-cache"
        outside.mkdir()
        victim = write_file(outside / "keep-me.txt", 23)
        moved = self.home / ".cache" / "app-original"
        real_scandir = os.scandir
        swapped = False

        def swap_before_scandir(target):
            nonlocal swapped
            if not swapped:
                swapped = True
                os.rename(self.cache, moved)
                os.symlink(outside, self.cache)
            return real_scandir(target)

        with patch("appssweep.safety.os.scandir", side_effect=swap_before_scandir):
            result = self.policy.clear_cache_target(str(self.cache))

        self.assertTrue(swapped)
        self.assertEqual(victim.read_bytes(), b"x" * 23)
        self.assertFalse(inside.exists())
        self.assertFalse((moved / "inside.bin").exists())
        self.assertEqual(result.errors, [])
        self.assertEqual(result.removed_files, 1)

    @unittest.skipIf(
        os.geteuid() == 0, "deterministic race tests require an unprivileged user"
    )
    def test_clear_cache_target_stays_anchored_for_swapped_nested_directory(
        self,
    ) -> None:
        nested = self.cache / "nested"
        inside = write_file(nested / "inside.bin", 256)
        outside = self.home / "outside-nested"
        outside.mkdir()
        victim = write_file(outside / "keep-me.txt", 23)
        moved = self.home / ".cache" / "app-nested-original"
        real_scandir = os.scandir
        calls = {"count": 0}

        def swap_on_nested_scandir(target):
            calls["count"] += 1
            if calls["count"] == 2:
                os.rename(nested, moved)
                os.symlink(outside, nested)
            return real_scandir(target)

        with patch("appssweep.safety.os.scandir", side_effect=swap_on_nested_scandir):
            result = self.policy.clear_cache_target(str(self.cache))

        self.assertGreaterEqual(calls["count"], 2)
        self.assertEqual(victim.read_bytes(), b"x" * 23)
        self.assertFalse(inside.exists())
        self.assertTrue(any("changed" in error for error in result.errors))

    @unittest.skipIf(
        os.geteuid() == 0, "deterministic race tests require an unprivileged user"
    )
    def test_clear_cache_target_refuses_target_swap_before_enumeration(self) -> None:
        write_file(self.cache / "inside.bin", 512)
        outside = self.home / "outside-refused"
        outside.mkdir()
        victim = write_file(outside / "keep-me.txt", 23)
        moved = self.home / ".cache" / "app-refused"
        original_verify = self.policy._verify_directory_path
        swapped = False

        def swap_then_verify(
            path: str,
            fd: int,
            expected_dev: int,
            expected_ino: int,
            description: str,
        ) -> None:
            nonlocal swapped
            if not swapped and path == str(self.cache):
                swapped = True
                os.rename(self.cache, moved)
                os.symlink(outside, self.cache)
            return original_verify(path, fd, expected_dev, expected_ino, description)

        with (
            patch.object(
                self.policy, "_verify_directory_path", side_effect=swap_then_verify
            ),
            self.assertRaises(SafetyError),
        ):
            self.policy.clear_cache_target(str(self.cache))

        self.assertTrue(swapped)
        self.assertEqual(victim.read_bytes(), b"x" * 23)
        self.assertTrue((moved / "inside.bin").exists())

    def test_clear_cache_target_removes_regular_file(self) -> None:
        cache_file = write_file(self.cache / "cache-file.bin", 321)

        result = self.policy.clear_cache_target(str(cache_file))

        self.assertFalse(cache_file.exists())
        self.assertEqual(result.removed_files, 1)
        self.assertEqual(result.bytes_removed, 321)
        self.assertEqual(result.errors, [])

    def test_clear_cache_target_skips_simulated_filesystem_boundary(self) -> None:
        boundary = self.cache / "boundary"
        inside = write_file(boundary / "inside.bin", 64)
        real_stat = os.stat

        def fake_stat(path, *args, **kwargs):
            result = real_stat(path, *args, **kwargs)
            if kwargs.get("dir_fd") is not None and os.fspath(path) == "boundary":
                return SimpleNamespace(
                    st_mode=result.st_mode,
                    st_dev=result.st_dev + 1,
                    st_ino=result.st_ino,
                    st_uid=result.st_uid,
                    st_size=result.st_size,
                )
            return result

        # Simulated device difference: a real secondary/bind mount was not
        # authorized in this environment. This verifies the policy branch.
        with patch("appssweep.safety.os.stat", side_effect=fake_stat):
            result = self.policy.clear_cache_target(str(self.cache))

        self.assertTrue(inside.exists())
        self.assertTrue(
            any("separate filesystem" in error for error in result.errors)
        )

    def test_clear_cache_target_skips_simulated_same_device_mount(self) -> None:
        boundary = self.cache / "bind-boundary"
        inside = write_file(boundary / "inside.bin", 64)
        calls = {"count": 0}

        def fake_mount_id(fd: int) -> int:
            calls["count"] += 1
            return 10 if calls["count"] == 1 else 11

        # Simulated same-device bind mount: no real mount was authorized.
        with patch.object(
            self.policy, "_mount_id_for_fd", side_effect=fake_mount_id
        ):
            result = self.policy.clear_cache_target(str(self.cache))

        self.assertGreaterEqual(calls["count"], 2)
        self.assertTrue(inside.exists())
        self.assertTrue(
            any("mount boundary" in error for error in result.errors)
        )

    def test_clear_cache_target_refuses_without_mount_boundary_verification(self) -> None:
        inside = write_file(self.cache / "inside.bin", 64)

        with (
            patch.object(self.policy, "_mount_id_for_fd", return_value=None),
            self.assertRaises(SafetyError),
        ):
            self.policy.clear_cache_target(str(self.cache))

        self.assertEqual(inside.read_bytes(), b"x" * 64)

    @unittest.skipIf(
        os.geteuid() == 0, "deterministic race tests require an unprivileged user"
    )
    def test_clear_cache_file_does_not_unlink_through_replaced_parent(self) -> None:
        cache_file = write_file(self.cache / "cache-file.bin", 23)
        outside = self.home / "outside-cache-file"
        outside.mkdir()
        victim = write_file(outside / "cache-file.bin", 23)
        moved = self.home / ".cache" / "app-file-original"
        real_unlink = os.unlink
        swapped = False

        def swap_before_unlink(path, *args, **kwargs):
            nonlocal swapped
            if not swapped:
                swapped = True
                os.rename(self.cache, moved)
                os.symlink(outside, self.cache)
            return real_unlink(path, *args, **kwargs)

        with patch("appssweep.safety.os.unlink", side_effect=swap_before_unlink):
            result = self.policy.clear_cache_target(str(cache_file))

        self.assertTrue(swapped)
        self.assertEqual(victim.read_bytes(), b"x" * 23)
        self.assertFalse((moved / "cache-file.bin").exists())
        self.assertEqual(result.removed_files, 1)

    @unittest.skipIf(
        os.geteuid() == 0, "deterministic race tests require an unprivileged user"
    )
    def test_remove_personal_file_does_not_unlink_through_replaced_parent(self) -> None:
        documents = self.home / "Documents"
        selected = write_file(documents / "report.txt", 23)
        outside = self.home / "outside-personal"
        outside.mkdir()
        victim = write_file(outside / "report.txt", 23)
        moved = self.home / "Documents-original"
        snapshot = snapshot_for(selected)
        real_unlink = os.unlink
        swapped = False

        def swap_before_unlink(path, *args, **kwargs):
            nonlocal swapped
            if not swapped:
                swapped = True
                os.rename(documents, moved)
                os.symlink(outside, documents)
            return real_unlink(path, *args, **kwargs)

        with patch("appssweep.safety.os.unlink", side_effect=swap_before_unlink):
            result = self.policy.remove_personal_file(snapshot)

        self.assertTrue(swapped)
        self.assertEqual(victim.read_bytes(), b"x" * 23)
        self.assertFalse((moved / "report.txt").exists())
        self.assertEqual(result.removed_files, 1)

    @unittest.skipIf(
        os.geteuid() == 0, "deterministic race tests require an unprivileged user"
    )
    def test_remove_personal_file_refuses_parent_swap_before_final_check(self) -> None:
        documents = self.home / "Documents"
        selected = write_file(documents / "report.txt", 23)
        outside = self.home / "outside-check"
        outside.mkdir()
        victim = write_file(outside / "report.txt", 23)
        moved = self.home / "Documents-check-original"
        snapshot = snapshot_for(selected)
        original_verify = self.policy._verify_directory_path
        swapped = False

        def swap_then_verify(
            path: str,
            fd: int,
            expected_dev: int,
            expected_ino: int,
            description: str,
        ) -> None:
            nonlocal swapped
            if not swapped and path == str(documents):
                swapped = True
                os.rename(documents, moved)
                os.symlink(outside, documents)
            return original_verify(path, fd, expected_dev, expected_ino, description)

        with (
            patch.object(
                self.policy, "_verify_directory_path", side_effect=swap_then_verify
            ),
            self.assertRaises(SafetyError),
        ):
            self.policy.remove_personal_file(snapshot)

        self.assertTrue(swapped)
        self.assertEqual(victim.read_bytes(), b"x" * 23)
        self.assertTrue((moved / "report.txt").exists())

    @unittest.skipIf(
        os.geteuid() == 0, "deterministic race tests require an unprivileged user"
    )
    def test_remove_personal_file_refuses_parent_replaced_by_real_directory(
        self,
    ) -> None:
        documents = self.home / "Documents"
        selected = write_file(documents / "report.txt", 23)
        outside = self.home / "outside-real-dir"
        outside.mkdir()
        write_file(outside / "report.txt", 23)
        moved = self.home / "Documents-real-original"
        snapshot = snapshot_for(selected)
        original_verify = self.policy._verify_directory_path
        swapped = False

        def swap_then_verify(
            path: str,
            fd: int,
            expected_dev: int,
            expected_ino: int,
            description: str,
        ) -> None:
            nonlocal swapped
            if not swapped and path == str(documents):
                swapped = True
                os.rename(documents, moved)
                os.rename(outside, documents)
            return original_verify(path, fd, expected_dev, expected_ino, description)

        with (
            patch.object(
                self.policy, "_verify_directory_path", side_effect=swap_then_verify
            ),
            self.assertRaises(SafetyError),
        ):
            self.policy.remove_personal_file(snapshot)

        self.assertTrue(swapped)
        self.assertEqual((documents / "report.txt").read_bytes(), b"x" * 23)
        self.assertEqual((moved / "report.txt").read_bytes(), b"x" * 23)

    @unittest.skipIf(
        os.geteuid() == 0, "deterministic race tests require an unprivileged user"
    )
    def test_remove_personal_file_refuses_changed_inode_at_final_check(self) -> None:
        selected = write_file(self.home / "Documents" / "report.txt", 23)
        snapshot = snapshot_for(selected)
        replacement = write_file(self.home / "replacement.bin", 23, byte=b"y")
        real_stat = os.stat
        swapped = False

        def replace_before_final_stat(path, *args, **kwargs):
            nonlocal swapped
            if (
                not swapped
                and kwargs.get("dir_fd") is not None
                and os.fspath(path) == selected.name
            ):
                swapped = True
                os.replace(replacement, selected)
            return real_stat(path, *args, **kwargs)

        with (
            patch("appssweep.safety.os.stat", side_effect=replace_before_final_stat),
            self.assertRaises(SafetyError),
        ):
            self.policy.remove_personal_file(snapshot)

        self.assertTrue(swapped)
        self.assertEqual(selected.read_bytes(), b"y" * 23)

    @unittest.skipIf(
        os.geteuid() == 0, "deterministic race tests require an unprivileged user"
    )
    def test_remove_personal_file_refuses_changed_mtime_at_final_check(self) -> None:
        selected = write_file(self.home / "Documents" / "report.txt", 23)
        snapshot = snapshot_for(selected)
        real_stat = os.stat
        mutated = False

        def modify_before_final_stat(path, *args, **kwargs):
            nonlocal mutated
            if (
                not mutated
                and kwargs.get("dir_fd") is not None
                and os.fspath(path) == selected.name
            ):
                mutated = True
                selected.write_bytes(b"z" * 23)
                changed = snapshot.mtime_ns + 1_000_000_000
                os.utime(selected, ns=(changed, changed))
            return real_stat(path, *args, **kwargs)

        with (
            patch("appssweep.safety.os.stat", side_effect=modify_before_final_stat),
            self.assertRaises(SafetyError),
        ):
            self.policy.remove_personal_file(snapshot)

        self.assertTrue(mutated)
        self.assertEqual(selected.read_bytes(), b"z" * 23)

    def test_authorization_covers_exact_plan(self) -> None:
        item = CleanupItem(
            key="cache:test",
            title="Test cache",
            group=RiskGroup.SAFE,
            size_bytes=10,
            summary="s",
            description="d",
            consequences="c",
            method="m",
            action=ActionKind.CLEAR_CACHE_CONTENTS,
        )
        other = CleanupItem(
            key="cache:other",
            title="Other cache",
            group=RiskGroup.SAFE,
            size_bytes=10,
            summary="s",
            description="d",
            consequences="c",
            method="m",
            action=ActionKind.CLEAR_CACHE_CONTENTS,
        )
        file = write_file(self.home / "Documents" / "movie.mp4", 100)
        snapshot = snapshot_for(file)
        auth = make_authorization([item], [snapshot], permanent_personal=False)
        ok, reason = authorization_covers(auth, [item], [snapshot], permanent_personal=False)
        self.assertTrue(ok, reason)
        self.assertFalse(
            authorization_covers(
                auth, [item, other], [snapshot], permanent_personal=False
            )[0]
        )
        self.assertFalse(
            authorization_covers(
                auth, [item], [snapshot], permanent_personal=True
            )[0]
        )

    def test_cache_item_engine_requires_authorization(self) -> None:
        item = CleanupItem(
            key="cache:test",
            title="Test cache",
            group=RiskGroup.SAFE,
            size_bytes=10,
            summary="s",
            description="d",
            consequences="c",
            method="m",
            action=ActionKind.CLEAR_CACHE_CONTENTS,
            paths=(str(self.cache),),
        )
        plan = CleanupPlan(cleanup_items=[item])
        engine = CleanupEngine(self.policy, filesystem_path=str(self.home))
        with self.assertRaises(PermissionError):
            engine.execute_plan(plan, Authorization(frozenset(), frozenset(), False))
        self.assertTrue(self.cache.exists())

    def test_authorization_cannot_cover_a_different_file(self) -> None:
        one = write_file(self.home / "Documents" / "one.bin", 10)
        two = write_file(self.home / "Documents" / "two.bin", 10)
        auth = make_authorization([], [snapshot_for(one)], permanent_personal=False)
        self.assertFalse(
            authorization_covers(
                auth, [], [snapshot_for(two)], permanent_personal=False
            )[0]
        )

    def test_lexical_path_boundary(self) -> None:
        self.assertTrue(is_within("/home/a/b", "/home/a"))
        self.assertFalse(is_within("/home/ab", "/home/a"))


if __name__ == "__main__":
    unittest.main()
