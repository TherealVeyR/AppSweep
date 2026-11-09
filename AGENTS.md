# AppSweep Development Instructions

This file is the permanent guide for anyone — human or AI — continuing work on
AppSweep. Read it before changing code.

## Project location

- Project root: `/home/Echo/Documents/Default Project/appssweep`
- Package: `appssweep/`
- Tests: `tests/`
- Launcher: `bin/appssweep`
- Workspace sibling `../taskvault` is an unrelated project. Never modify,
  delete, format, or test it while working on AppSweep.

## Read these first

1. `AGENTS.md` (this file): rules and workflow.
2. `PROGRESS.md`: what is implemented and verified.
3. `TODO.md`: outstanding work and known bugs.
4. `ARCHITECTURE.md`: module map, data flow, and design decisions.
5. `README.md`: user-facing installation and usage.
6. `CHANGELOG.md`: notable verified changes.
7. `CONTRIBUTING.md`: development, testing, and Git workflow.
8. `RELEASE_CHECKLIST.md`: release gates that still need evidence.

`temp_task.md` is the scratch record of the current work session. Update it
when starting, pausing, or substantially changing a task.

## Safety invariants — do not weaken

These are product requirements, not optional hardening:

1. **Scanning is read-only.** No scan, discovery, or export step may modify,
   move, or delete scanned data.
2. **Report-only by default.** A non-interactive run must not offer cleanup.
   Cleanup requires `--interactive` and an explicit terminal confirmation.
3. **No deletion without confirmation.** A plan only executes after the user
   types `CLEAN`.
4. **Personal files default to Trash.** Never add a silent fallback to
   permanent deletion. Permanent deletion requires the separate
   `DELETE PERMANENTLY` phrase and the `permanent_personal` authorization flag.
5. **No shell.** Never use `shell=True`, `os.system`, `eval`, or string-built
   commands. Commands are argument tuples and are validated immediately before
   execution.
6. **No broad recursive deletion.** Do not add `rm -rf`, arbitrary path
   deletion, or recursive cleanup outside the allowlisted, validated cache
   logic. System cleanup is delegated to official tools.
7. **Never run the whole app as root.** Refuse euid 0. Use `pkexec` only for a
   specific allowlisted command that needs administrator approval.
8. **No symlink following.** Scanning and cleanup must use `lstat` /
   `follow_symlinks=False`; symlinks are reported, never traversed as targets.
9. **Revalidate before acting.** Personal files must match path, device, inode,
   size, mtime, owner, and risk group from the scan snapshot.
10. **Protect sensitive locations.** Do not add cleanup targets under system
    trees, home configuration, SSH/GPG/PKI data, Trash internals, or top-level
    cache roots. The only allowed cache override is Flatpak's `.var` app-cache
    area; never extend it to `.ssh`, `.gnupg`, `.local`, or similar trees.
11. **Escape untrusted text.** All filesystem-derived strings go through
    `safe_terminal_text` before display.
12. **Neutralize spreadsheet formulas.** CSV text goes through
    `csv_safe_cell` before writing.
13. **Keep selection mapping exact.** Never re-sort a displayed numbered list
    before resolving a selection. `tests/test_ui.py` guards the top-50 mapping.
14. **Cancelled scans disable cleanup.** Preserve partial reports for export,
    but no destructive action may run.
15. **Audit confirmed cleanup attempts.** Record the exact plan before execution
    and the result or refusal afterward in the private append-only audit log.
    A failed audit write is surfaced as a warning; it must not be silently
    ignored or allowed to hide a cleanup result.
16. **Do not claim completion without evidence.** "Done" means implemented,
    tested, and documented. A skipped test is not a passing test.

## Technology stack

- Python 3.10+; Fedora Linux is the supported target.
- Standard library only at runtime.
- Optional Rich rendering behind a guarded import.
- `unittest` for tests, with `subprocess` mocked where host tools are absent.
- `setuptools`/`pyproject.toml` packaging and a console-script entry point.

## Development rules

- Support Python 3.10+ and Fedora. Keep the standard library as the only
  required dependency.
- Rich is optional. Both the ANSI interface and the Rich interface must work;
  do not make Rich mandatory.
- Use simple, direct user-facing language. Avoid jargon when a plain phrase
  works.
- Keep filesystem-derived strings out of executable command construction.
- Keep cleanup actions in the `ActionKind` allowlist. Adding an action requires:
  a discovery path, command-shape validation, authorization coverage, a test,
  and documentation.
- Prefer small, focused modules over adding responsibilities to `cli.py`.
- Preserve the three user-facing groups: **SAFE**, **CAREFUL**, **REBUILD**.
- Keep the 50-file personal report separate from numbered cleanup categories.
- Write reports atomically and keep exported files private (`0600`).
- Keep the cleanup audit log private (`0700` directory, `0600` file) and append
  only; never log to a symlink or an unsafe shared directory.
- `--dry-run` is an alias for `--report-only`; it must never offer or execute
  cleanup.
- Handle permission errors and skipped mounts explicitly; never pretend
  coverage is complete.

## Git rules

- The repository root is this `appssweep/` directory. Do not initialize a
  nested repository in the parent workspace or in `../taskvault`.
- Use conventional commits at meaningful, verified milestones, for example
  `feat:`, `fix:`, `test:`, `docs:`, or `refactor:`.
- Before committing, inspect the diff, run the relevant tests, check for
  secrets/personal data, and verify only intended files are staged.
- Never commit virtual environments, build output, test caches, generated scan
  reports, audit logs, credentials, or machine-specific configuration.
- Never rewrite history. Never push, tag, or publish without explicit
  permission.
- Keep the working tree clean after a completed milestone.

## Testing protocol

Run from the project root:

```bash
python3 -m compileall -q appssweep tests
python3 -m unittest discover -v
```

Current baseline (2026-11-09):

- 124 tests total.
- 123 pass and 1 Rich-only test is skipped when Rich is not installed.
- All 124 pass in a venv with `rich` installed.

Rules for tests:

- Use `unittest`, temporary directories, and the fakes in `tests/helpers.py`.
- Never perform destructive operations against the real host during tests.
- Horizontal tests should cover traversal, symlinks, permissions, mount
  boundaries, hardlink accounting, package/Flatpak detection, selection
  parsing, authorization, cancellation, path safety, storage calculations, and
  reporting.
- Mock `dnf`, `rpm-ostree`, `flatpak`, `journalctl`, and `gio` when host tools
  are unavailable.
- Do not weaken or delete a safety test to make a change pass.
- Add a regression test with every bug fix.

Optional Rich verification:

```bash
python3 -m venv /tmp/appssweep-rich-venv
/tmp/appssweep-rich-venv/bin/pip install ".[rich]"
/tmp/appssweep-rich-venv/bin/python -m unittest discover -v
```

## Documentation protocol

After each major milestone, update:

- `PROGRESS.md`: what changed and how it was verified.
- `TODO.md`: remaining work, risks, and bugs.
- `CHANGELOG.md`: notable user-visible or safety-relevant changes.
- `README.md` and `ARCHITECTURE.md` when behavior or design changes.
- `temp_task.md`: the current session's active task and next command.

Do not record a feature as complete based only on intent. Cite the test or
smoke check that proves it. Keep `RELEASE_CHECKLIST.md` unchecked until the
evidence exists, especially for real-Fedora-only items.

## Useful commands

```bash
# Source checkout
./bin/appssweep --mode quick
./bin/appssweep --mode full --report-only
./bin/appssweep --mode full --dry-run
./bin/appssweep --mode custom --path ~/Downloads --json report.json

# Install and verify packaging
python3 -m venv .venv
. .venv/bin/activate
pip install .
appssweep --version

# Lint (optional; ruff is not a project dependency)
ruff check appssweep tests
```

## Environment caveats

The development sandbox is a Freedesktop Flatpak runtime, not a full Fedora
host. It may lack `dnf`, `rpm-ostree`, `flatpak`, `journalctl`, `rpm`, and
`pkexec`; `gio` may be present but is not a substitute for validating real host
Trash behavior. Host-command behavior is therefore covered with mocks. A release
candidate must still be validated on a real Fedora host before claiming full
system-tool coverage.

The scanner deliberately skips tmpfs, virtual, network, and external mounts
during a full storage scan. A custom scan root inside such a mount is allowed
because the user explicitly selected it. Separately mounted local `/opt`,
`/etc`, `/root`, `/srv`, and disk-backed `/tmp` are included, as is a read-only
`/usr` image on immutable Fedora systems. Temporary data under `/tmp` and
`/var/tmp` is still analyzed by the report-only temporary-file discovery step;
memory-backed tmpfs is not counted as disk storage.

## Definition of done

A change is complete only when:

1. The implementation is in place.
2. Relevant tests pass, including new regression coverage.
3. `python3 -m compileall -q appssweep tests` succeeds.
4. User-facing text and README/docs remain accurate.
5. `PROGRESS.md`, `TODO.md`, and `CHANGELOG.md` are updated as appropriate.
6. The diff has been reviewed and only intended files are staged.
7. No unrelated project files, especially `../taskvault`, were touched.
