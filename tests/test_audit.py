"""Cleanup audit-log privacy, formatting, and refusal tests."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from appssweep.audit import append_cleanup_event, audit_log_path
from appssweep.models import (
    ActionKind,
    CleanupItem,
    CleanupPlan,
    ExecutionReport,
    OperationResult,
    RiskGroup,
)
from appssweep.safety import make_authorization


class AuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.state = self.root / "state"
        self.env_patch = patch.dict(
            os.environ,
            {"XDG_STATE_HOME": str(self.state)},
        )
        self.env_patch.start()
        self.item = CleanupItem(
            key="cache:test",
            title="Test cache",
            group=RiskGroup.SAFE,
            size_bytes=2048,
            summary="Test cache.",
            description="A test cache.",
            consequences="It can be rebuilt.",
            method="Clear the contents.",
            action=ActionKind.CLEAR_CACHE_CONTENTS,
            paths=(str(self.root / "cache"),),
        )
        self.plan = CleanupPlan(cleanup_items=[self.item])
        self.authorization = make_authorization(
            [self.item], [], permanent_personal=False
        )

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.tmp.cleanup()

    def test_audit_log_is_private_and_records_plan_and_result(self) -> None:
        execution = ExecutionReport(
            results=[
                OperationResult(
                    title="Test cache",
                    success=True,
                    reclaimed_bytes=1024,
                    message="Cleared cache.",
                )
            ],
            available_before=5000,
            available_after=6000,
            filesystem_path=str(self.home),
        )

        self.assertIsNone(
            append_cleanup_event(
                "cleanup_started", self.plan, self.authorization, home=str(self.home)
            )
        )
        self.assertIsNone(
            append_cleanup_event(
                "cleanup_finished",
                self.plan,
                self.authorization,
                home=str(self.home),
                execution=execution,
            )
        )

        path = Path(audit_log_path(str(self.home)))
        self.assertEqual(path, self.state / "appssweep" / "cleanup-audit.log")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        records = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(
            [record["event"] for record in records],
            ["cleanup_started", "cleanup_finished"],
        )
        self.assertEqual(records[0]["app"], "AppSweep")
        self.assertEqual(records[0]["plan"]["cleanup_total"], 2048)
        self.assertEqual(records[1]["execution"]["available_after"], 6000)
        self.assertEqual(
            records[1]["execution"]["filesystem_path"], str(self.home)
        )

    def test_audit_log_refuses_a_symlink_target(self) -> None:
        parent = self.state / "appssweep"
        parent.mkdir(parents=True, mode=0o700)
        victim = self.root / "victim.txt"
        victim.write_text("do not touch\n", encoding="utf-8")
        (parent / "cleanup-audit.log").symlink_to(victim)

        error = append_cleanup_event(
            "cleanup_started", self.plan, self.authorization, home=str(self.home)
        )

        self.assertIsNotNone(error)
        self.assertEqual(victim.read_text(encoding="utf-8"), "do not touch\n")

    def test_audit_log_refuses_a_hard_link_to_another_file(self) -> None:
        parent = self.state / "appssweep"
        parent.mkdir(parents=True, mode=0o700)
        victim = self.root / "victim.txt"
        victim.write_text("do not touch\n", encoding="utf-8")
        os.link(victim, parent / "cleanup-audit.log")

        error = append_cleanup_event(
            "cleanup_started", self.plan, self.authorization, home=str(self.home)
        )

        self.assertIsNotNone(error)
        self.assertIn("hard link", error)
        self.assertEqual(victim.read_text(encoding="utf-8"), "do not touch\n")

    def test_audit_log_refuses_group_or_world_writable_folder(self) -> None:
        parent = self.state / "appssweep"
        parent.mkdir(parents=True, mode=0o777)
        parent.chmod(0o777)

        error = append_cleanup_event(
            "cleanup_started", self.plan, self.authorization, home=str(self.home)
        )

        self.assertIsNotNone(error)
        self.assertIn("writable", error)

    def test_audit_path_falls_back_to_user_state_home(self) -> None:
        with patch.dict(os.environ, {"XDG_STATE_HOME": ""}):
            path = audit_log_path(str(self.home))
        self.assertEqual(
            path,
            str(self.home / ".local" / "state" / "appssweep" / "cleanup-audit.log"),
        )


if __name__ == "__main__":
    unittest.main()
