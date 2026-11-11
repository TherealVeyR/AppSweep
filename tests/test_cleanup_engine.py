"""Mocked cleanup execution tests, with no destructive host commands."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from appsweep.cleanup_engine import CleanupEngine
from appsweep.models import (
    ActionKind,
    CleanupItem,
    CleanupPlan,
    RiskGroup,
)
from appsweep.runner import CommandRunner
from appsweep.safety import SafetyPolicy, Validation, make_authorization

from .helpers import snapshot_for, write_file


def completed(args, code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=args, returncode=code, stdout=stdout, stderr=stderr)


def cache_item(path: str, key="cache:test") -> CleanupItem:
    return CleanupItem(
        key=key,
        title="Test cache",
        group=RiskGroup.SAFE,
        size_bytes=10,
        summary="s",
        description="d",
        consequences="c",
        method="m",
        action=ActionKind.CLEAR_CACHE_CONTENTS,
        paths=(path,),
    )


class CleanupEngineTests(unittest.TestCase):
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

    def engine(self, runner=None) -> CleanupEngine:
        return CleanupEngine(
            self.policy,
            runner or CommandRunner(which=lambda name: None),
            filesystem_path=str(self.home),
        )

    def test_cache_cleanup_executes_after_authorization(self) -> None:
        write_file(self.cache / "inside.bin", 512)
        item = cache_item(str(self.cache))
        plan = CleanupPlan(cleanup_items=[item])
        auth = make_authorization([item], [], permanent_personal=False)

        execution = self.engine().execute_plan(plan, auth)

        self.assertEqual(execution.succeeded, 1)
        self.assertEqual(execution.failed, 0)
        self.assertEqual(execution.filesystem_path, str(self.home))
        self.assertFalse((self.cache / "inside.bin").exists())
        self.assertTrue(self.cache.is_dir())

    def test_failed_authorization_changes_nothing(self) -> None:
        write_file(self.cache / "inside.bin", 512)
        item = cache_item(str(self.cache))
        plan = CleanupPlan(cleanup_items=[item])
        auth = make_authorization([], [], permanent_personal=False)

        with self.assertRaises(PermissionError):
            self.engine().execute_plan(plan, auth)
        self.assertTrue((self.cache / "inside.bin").exists())

    def test_duplicate_cleanup_keys_are_rejected(self) -> None:
        item = cache_item(str(self.cache))
        plan = CleanupPlan(cleanup_items=[item, item])
        auth = make_authorization([item], [], permanent_personal=False)
        with self.assertRaises(ValueError):
            self.engine().execute_plan(plan, auth)

    def test_duplicate_personal_paths_are_rejected(self) -> None:
        file = write_file(self.home / "Documents" / "movie.mp4", 1024)
        snapshot = snapshot_for(file)
        plan = CleanupPlan(personal_files=[snapshot, snapshot])
        auth = make_authorization([], [snapshot], permanent_personal=False)
        with self.assertRaises(ValueError):
            self.engine().execute_plan(plan, auth)

    def test_personal_file_uses_gio_trash_not_direct_delete(self) -> None:
        file = write_file(self.home / "Documents" / "movie.mp4", 1024)
        snapshot = snapshot_for(file)
        calls = []

        def handler(args, **kwargs):
            calls.append(tuple(args))
            return completed(args, code=0)

        runner = CommandRunner(
            which=lambda name: "/usr/bin/gio" if name == "gio" else None,
            run_fn=lambda args, **kwargs: handler(args, **kwargs),
        )
        plan = CleanupPlan(personal_files=[snapshot])
        auth = make_authorization([], [snapshot], permanent_personal=False)

        execution = self.engine(runner).execute_plan(plan, auth)

        self.assertEqual(execution.succeeded, 1)
        self.assertTrue(file.exists(), "the test double does not move files")
        self.assertEqual(calls[0][:3], ("/usr/bin/gio", "trash", "--"))
        self.assertEqual(calls[0][3], str(file))

    def test_personal_file_is_not_deleted_when_gio_is_missing(self) -> None:
        file = write_file(self.home / "Documents" / "movie.mp4", 1024)
        snapshot = snapshot_for(file)
        plan = CleanupPlan(personal_files=[snapshot])
        auth = make_authorization([], [snapshot], permanent_personal=False)

        with patch("appsweep.cleanup_engine.os.unlink") as unlink:
            execution = self.engine(
                CommandRunner(which=lambda name: None)
            ).execute_plan(plan, auth)

        self.assertEqual(execution.failed, 1)
        self.assertTrue(file.exists())
        unlink.assert_not_called()

    def test_gio_handoff_revalidates_and_refuses_a_changed_path(self) -> None:
        file = write_file(self.home / "Documents" / "movie.mp4", 1024)
        snapshot = snapshot_for(file)
        calls = []

        def handler(args, **kwargs):
            calls.append(tuple(args))
            return completed(args, code=0)

        runner = CommandRunner(
            which=lambda name: "/usr/bin/gio" if name == "gio" else None,
            run_fn=lambda args, **kwargs: handler(args, **kwargs),
        )
        plan = CleanupPlan(personal_files=[snapshot])
        auth = make_authorization([], [snapshot], permanent_personal=False)

        with patch.object(
            self.policy,
            "validate_personal_snapshot",
            side_effect=[
                Validation(True, path=str(file)),
                Validation(False, "changed after the plan was authorized"),
            ],
        ):
            execution = self.engine(runner).execute_plan(plan, auth)

        self.assertEqual(execution.failed, 1)
        self.assertEqual(calls, [])
        self.assertTrue(file.exists())
        self.assertIn("Revalidation failed", execution.results[0].message)

    def test_gio_failure_does_not_fall_back_to_unlink(self) -> None:
        file = write_file(self.home / "Documents" / "movie.mp4", 1024)
        snapshot = snapshot_for(file)
        runner = CommandRunner(
            which=lambda name: "/usr/bin/gio" if name == "gio" else None,
            run_fn=lambda args, **kwargs: completed(args, code=1, stderr="trash failed"),
        )
        plan = CleanupPlan(personal_files=[snapshot])
        auth = make_authorization([], [snapshot], permanent_personal=False)

        with patch("appsweep.cleanup_engine.os.unlink") as unlink:
            execution = self.engine(runner).execute_plan(plan, auth)

        self.assertEqual(execution.failed, 1)
        self.assertTrue(file.exists())
        unlink.assert_not_called()
        self.assertIn("did not fall back to permanent deletion", execution.results[0].message)

    def test_permanent_personal_is_disabled_even_with_authorization(self) -> None:
        file = write_file(self.home / "Documents" / "old.iso", 2048)
        original = file.read_bytes()
        snapshot = snapshot_for(file)
        plan = CleanupPlan(personal_files=[snapshot], permanent_personal=True)
        auth = make_authorization([], [snapshot], permanent_personal=True)

        with (
            patch.object(
                self.policy, "_remove_personal_file_quarantine_legacy"
            ) as legacy,
            self.assertRaises(PermissionError) as raised,
        ):
            self.engine().execute_plan(plan, auth)

        self.assertIn("disabled", str(raised.exception))
        legacy.assert_not_called()
        self.assertTrue(file.exists())
        self.assertEqual(file.read_bytes(), original)

    def test_invalid_command_shape_is_refused_before_running(self) -> None:
        item = CleanupItem(
            key="dnf-cache",
            title="DNF cache",
            group=RiskGroup.REBUILD,
            size_bytes=10,
            summary="s",
            description="d",
            consequences="c",
            method="m",
            action=ActionKind.DNF_CLEAN_CACHE,
            command_args=(("dnf", "clean", "--all"),),
            needs_admin=True,
        )
        called = {"run": False}

        def handler(args, **kwargs):
            called["run"] = True
            return completed(args, code=0)

        runner = CommandRunner(
            which=lambda name: "/usr/bin/dnf",
            run_fn=lambda args, **kwargs: handler(args, **kwargs),
        )
        plan = CleanupPlan(cleanup_items=[item])
        auth = make_authorization([item], [], permanent_personal=False)

        execution = self.engine(runner).execute_plan(plan, auth)

        self.assertEqual(execution.failed, 1)
        self.assertFalse(called["run"])
        self.assertIn("safety check", execution.results[0].message)

    def test_ineligible_item_is_refused(self) -> None:
        item = cache_item(str(self.cache))
        item.eligible = False
        plan = CleanupPlan(cleanup_items=[item])
        auth = make_authorization([item], [], permanent_personal=False)
        with self.assertRaises(PermissionError):
            self.engine().execute_plan(plan, auth)

    def test_empty_plan_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.engine().execute_plan(
                CleanupPlan(),
                make_authorization([], [], permanent_personal=False),
            )


if __name__ == "__main__":
    unittest.main()
