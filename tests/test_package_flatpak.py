"""Package-manager, Flatpak, command-runner, and command-shape tests."""

from __future__ import annotations

import subprocess
import unittest

from appssweep.cleanup_engine import validate_item_command
from appssweep.flatpak import (
    _parse_runtime_line,
    _used_aliases,
    discover_flatpak,
)
from appssweep.models import ActionKind, CleanupItem, RiskGroup
from appssweep.package_manager import (
    PackageManagerInfo,
    detect_package_manager,
    discover_package_cleanup,
    kernel_exclusion_args,
    parse_autoremove_preview,
    preview_autoremove,
    running_kernel_release,
    version_sort_key,
)
from appssweep.runner import CommandRunner


def completed(args, code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=args, returncode=code, stdout=stdout, stderr=stderr)


def runner(mapping, handler):
    return CommandRunner(
        which=lambda name: mapping.get(name),
        run_fn=lambda args, **kwargs: handler(args, **kwargs),
        euid=1000,
    )


def item(action: ActionKind, command_args, **kwargs) -> CleanupItem:
    return CleanupItem(
        key="test",
        title="Test",
        group=RiskGroup.REBUILD,
        size_bytes=1,
        summary="s",
        description="d",
        consequences="c",
        method="m",
        action=action,
        command_args=command_args,
        **kwargs,
    )


class RunnerTests(unittest.TestCase):
    def test_run_uses_argument_list_and_never_shell(self) -> None:
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return completed(args, stdout="ok")

        command_runner = CommandRunner(
            which=lambda name: "/usr/bin/true",
            run_fn=fake_run,
            euid=1000,
        )
        result = command_runner.run(["true", "a b"], timeout=7)
        self.assertTrue(result.ok)
        self.assertEqual(captured["args"], ["true", "a b"])
        self.assertIs(captured["kwargs"]["shell"], False)
        self.assertIs(captured["kwargs"]["capture_output"], True)
        self.assertEqual(captured["kwargs"]["timeout"], 7)

    def test_pkexec_is_added_only_for_system_operations(self) -> None:
        mapping = {"pkexec": "/usr/bin/pkexec"}
        as_user = CommandRunner(which=lambda name: mapping.get(name), euid=1000)
        self.assertEqual(
            as_user.build_command(("dnf", "clean", "all"), needs_admin=True),
            ("/usr/bin/pkexec", "dnf", "clean", "all"),
        )
        as_root = CommandRunner(which=lambda name: mapping.get(name), euid=0)
        self.assertEqual(
            as_root.build_command(("dnf", "clean", "all"), needs_admin=True),
            ("dnf", "clean", "all"),
        )

    def test_missing_pkexec_is_reported_without_running(self) -> None:
        command_runner = CommandRunner(which=lambda name: None, euid=1000)
        result = command_runner.run(("dnf", "clean", "all"), needs_admin=True)
        self.assertFalse(result.ok)
        self.assertIn("pkexec", result.stderr)
        self.assertFalse(result.launched)


class PackageManagerTests(unittest.TestCase):
    def test_detect_dnf5(self) -> None:
        def handler(args, **kwargs):
            if "--version" in args:
                return completed(args, stdout="dnf5 version 5.2.10.0\n")
            return completed(args, code=1)

        info = detect_package_manager(
            runner({"dnf5": "/usr/bin/dnf5"}, handler),
            ostree_marker="/nonexistent/appsweep-ostree",
        )
        self.assertEqual(info.kind, "dnf5")
        self.assertEqual(info.binary, "/usr/bin/dnf5")
        self.assertTrue(info.available)
        self.assertIn("5.2", info.version)

    def test_detect_no_package_manager(self) -> None:
        info = detect_package_manager(
            runner({}, lambda args, **kwargs: completed(args, code=1)),
            ostree_marker="/nonexistent/appsweep-ostree",
        )
        self.assertEqual(info.kind, "none")
        self.assertFalse(info.available)

    def test_parse_autoremove_preview_finds_packages_and_size(self) -> None:
        text = """
Removing:
 kernel-core-6.8.5-301.fc40.x86_64    1.2 M
 httpd-2.4.62-1.fc40.x86_64          500 k

Transaction Summary
Freed space: 1.2 GB
"""
        preview = parse_autoremove_preview(text)
        self.assertTrue(preview.ok)
        self.assertIn("kernel-core-6.8.5-301.fc40", preview.package_names)
        self.assertTrue(preview.mentions_kernel)
        self.assertEqual(preview.freed_bytes, int(1.2 * 1024**3))

    def test_parse_autoremove_preview_nothing_to_do(self) -> None:
        preview = parse_autoremove_preview("Nothing to do.")
        self.assertTrue(preview.ok)
        self.assertFalse(preview.has_work)
        self.assertFalse(preview.mentions_kernel)

    def test_preview_autoremove_uses_cacheonly_by_default(self) -> None:
        calls = []

        def handler(args, **kwargs):
            calls.append(tuple(args))
            return completed(
                args,
                stdout="Removing:\n httpd-2.4.62-1.fc40.x86_64 1.0 M\n",
            )

        info = PackageManagerInfo(kind="dnf5", binary="/usr/bin/dnf5")
        preview = preview_autoremove(
            runner({"dnf5": "/usr/bin/dnf5"}, handler),
            info,
            current_release="6.8.5-301.fc40.x86_64",
            allow_metadata_refresh=False,
        )
        self.assertTrue(preview.ok)
        self.assertEqual(len(calls), 1)
        self.assertIn("--cacheonly", calls[0])
        self.assertNotIn("--refresh", calls[0])

    def test_package_discovery_without_cache_or_tools(self) -> None:
        discovery = discover_package_cleanup(
            runner(
                {"dnf5": "/usr/bin/dnf5"},
                lambda args, **kwargs: completed(args, code=1),
            ),
            dnf_cache_dirs=(),
            enable_autoremove=False,
            enable_kernels=False,
        )
        self.assertEqual(discovery.items, [])
        self.assertEqual(discovery.manager.kind, "dnf5")
        self.assertTrue(discovery.notes)

    def test_version_sort_key(self) -> None:
        values = ["6.8.5-301.fc40", "6.9.1-200.fc40", "6.8.10-100.fc40"]
        self.assertEqual(
            max(values, key=version_sort_key),
            "6.9.1-200.fc40",
        )

    def test_running_kernel_and_exclusion_arguments(self) -> None:
        release = running_kernel_release()
        self.assertTrue(release)
        excludes = kernel_exclusion_args(release)
        self.assertTrue(excludes)
        self.assertIn(release, excludes[0])


class CommandShapeTests(unittest.TestCase):
    def test_dnf_clean_cache_shape(self) -> None:
        valid = item(ActionKind.DNF_CLEAN_CACHE, (("dnf5", "clean", "all"),))
        self.assertIsNone(validate_item_command(valid))
        invalid = item(ActionKind.DNF_CLEAN_CACHE, (("dnf5", "clean", "--all"),))
        self.assertIsNotNone(validate_item_command(invalid))

    def test_journal_vacuum_shape(self) -> None:
        valid = item(
            ActionKind.JOURNAL_VACUUM,
            (("journalctl", "--vacuum-size=256M"),),
        )
        self.assertIsNone(validate_item_command(valid))
        invalid = item(
            ActionKind.JOURNAL_VACUUM,
            (("journalctl", "--vacuum-size=256M", "--rotate"),),
        )
        self.assertIsNotNone(validate_item_command(invalid))

    def test_rpm_ostree_metadata_only_shape(self) -> None:
        valid = item(ActionKind.RPM_OSTREE_METADATA, (("rpm-ostree", "cleanup", "-m"),))
        self.assertIsNone(validate_item_command(valid))
        invalid = item(ActionKind.RPM_OSTREE_METADATA, (("rpm-ostree", "cleanup", "-b"),))
        self.assertIsNotNone(validate_item_command(invalid))

    def test_flatpak_only_unused_and_one_installation(self) -> None:
        valid = item(
            ActionKind.FLATPAK_UNUSED,
            (("flatpak", "--user", "uninstall", "--unused", "--assumeyes"),),
        )
        self.assertIsNone(validate_item_command(valid))
        explicit = item(
            ActionKind.FLATPAK_UNUSED,
            (("flatpak", "--user", "uninstall", "org.example.App", "--assumeyes"),),
        )
        self.assertIsNotNone(validate_item_command(explicit))
        system_without_admin = item(
            ActionKind.FLATPAK_UNUSED,
            (("flatpak", "--system", "uninstall", "--unused", "--assumeyes"),),
            needs_admin=False,
        )
        self.assertIsNotNone(validate_item_command(system_without_admin))

    def test_autoremove_must_exclude_running_kernel(self) -> None:
        release = running_kernel_release()
        args = (
            "dnf",
            "autoremove",
            "--assumeyes",
            *kernel_exclusion_args(release),
        )
        valid = item(
            ActionKind.DNF_AUTOREMOVE,
            (args,),
            metadata={"package_names": ["httpd"], "running_kernel": release},
        )
        self.assertIsNone(validate_item_command(valid))
        no_exclude = item(
            ActionKind.DNF_AUTOREMOVE,
            (("dnf", "autoremove", "--assumeyes"),),
            metadata={"package_names": ["httpd"]},
        )
        self.assertIsNotNone(validate_item_command(no_exclude))


class FlatpakTests(unittest.TestCase):
    def test_parse_runtime_line_and_aliases(self) -> None:
        parsed = _parse_runtime_line(
            "org.gnome.Platform/x86_64/46\t1.2 GB\tuser"
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.ref, "org.gnome.Platform/x86_64/46")
        self.assertEqual(parsed.size, int(1.2 * 1024**3))
        aliases = _used_aliases({"runtime/org.gnome.Platform/x86_64/46"})
        self.assertIn("runtime/org.gnome.Platform/x86_64/46", aliases)
        self.assertIn("org.gnome.Platform/x86_64/46", aliases)

    def test_discover_flatpak_unused_runtime(self) -> None:
        def handler(args, **kwargs):
            if "--version" in args:
                return completed(args, stdout="Flatpak 1.18.4\n")
            if "list" in args and "--app" in args:
                return completed(
                    args,
                    stdout="org.example.App\torg.gnome.Platform/x86_64/46\n",
                )
            if "list" in args and "--runtime" in args:
                return completed(
                    args,
                    stdout=(
                        "org.gnome.Platform/x86_64/46\t1.2 GB\tuser\n"
                        "org.gnome.Platform/x86_64/45\t900 MB\tuser\n"
                    ),
                )
            return completed(args, code=1)

        discovery = discover_flatpak(
            runner({"flatpak": "/usr/bin/flatpak"}, handler),
            user_install=True,
            system_install=False,
        )
        self.assertEqual(len(discovery.items), 1)
        item = discovery.items[0]
        self.assertEqual(item.key, "flatpak-unused-user")
        self.assertGreater(item.size_bytes, 0)
        self.assertTrue(item.estimated)
        self.assertEqual(item.action, ActionKind.FLATPAK_UNUSED)
        self.assertIsNone(validate_item_command(item))

    def test_flatpak_unavailable_is_not_an_error(self) -> None:
        discovery = discover_flatpak(
            runner({}, lambda args, **kwargs: completed(args, code=1))
        )
        self.assertEqual(discovery.items, [])
        self.assertFalse(discovery.info.available)
        self.assertTrue(discovery.notes)


if __name__ == "__main__":
    unittest.main()
