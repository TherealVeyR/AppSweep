"""Safe subprocess abstraction.

All commands are argument lists. ``shell=True`` is never used. Commands that
need system privileges ask for them at the point of use with pkexec instead of
running the whole application as root.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .util import format_command


@dataclass
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    launched: bool = True
    exception: str = ""

    @property
    def ok(self) -> bool:
        return self.launched and not self.timed_out and self.returncode == 0

    @property
    def output(self) -> str:
        return "\n".join(part for part in (self.stdout.strip(), self.stderr.strip()) if part)

    @property
    def command(self) -> str:
        return format_command(self.args)


WhichFn = Callable[[str], str | None]


class CommandRunner:
    """A very small subprocess wrapper with explicit timeouts."""

    def __init__(
        self,
        *,
        which: WhichFn | None = None,
        run_fn: Callable[..., subprocess.CompletedProcess] | None = None,
        euid: int | None = None,
    ) -> None:
        self._which = which or shutil.which
        self._run = run_fn or subprocess.run
        self.euid = os.geteuid() if euid is None else euid

    def find(self, name: str) -> str | None:
        return self._which(name)

    def available(self, name: str) -> bool:
        return self.find(name) is not None

    def build_command(
        self,
        args: Iterable[str],
        *,
        needs_admin: bool = False,
    ) -> tuple[str, ...]:
        argv = tuple(str(arg) for arg in args)
        if not argv:
            raise ValueError("command cannot be empty")
        if needs_admin and self.euid != 0:
            pkexec = self.find("pkexec")
            if pkexec is None:
                raise RuntimeError(
                    "This action needs administrator access, but pkexec is not available."
                )
            return (pkexec, *argv)
        return argv

    def run(
        self,
        args: Iterable[str],
        *,
        needs_admin: bool = False,
        timeout: int = 60,
        stdin_text: str | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> CommandResult:
        try:
            argv = self.build_command(args, needs_admin=needs_admin)
        except (RuntimeError, ValueError) as exc:
            return CommandResult(
                args=tuple(args),
                returncode=127,
                stderr=str(exc),
                launched=False,
                exception=str(exc),
            )

        env = os.environ.copy()
        env.update(
            {
                "LC_ALL": "C",
                "LANG": "C",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        if extra_env:
            env.update(extra_env)

        try:
            completed = self._run(
                list(argv),
                input=stdin_text,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = _as_text(getattr(exc, "stdout", ""))
            stderr = _as_text(getattr(exc, "stderr", ""))
            return CommandResult(
                args=argv,
                returncode=124,
                stdout=stdout,
                stderr=stderr or f"Command timed out after {timeout}s.",
                timed_out=True,
            )
        except FileNotFoundError as exc:
            return CommandResult(
                args=argv,
                returncode=127,
                stderr=str(exc),
                launched=False,
                exception=str(exc),
            )
        except OSError as exc:
            return CommandResult(
                args=argv,
                returncode=126,
                stderr=str(exc),
                launched=False,
                exception=str(exc),
            )

        return CommandResult(
            args=argv,
            returncode=int(completed.returncode),
            stdout=_as_text(completed.stdout),
            stderr=_as_text(completed.stderr),
        )


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
