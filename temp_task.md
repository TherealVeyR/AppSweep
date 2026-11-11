# Current Task Cache

Last updated: 2026-11-11

## Active task

Finish the final security remediation from `FINAL_SECURITY_AUDIT.md`, make the
source repository publishable, and record exact evidence. Do **not** commit,
push, reset, rebase, amend, stash, clean, or rewrite history unless explicitly
authorized.

Status: implementation, documentation, publication files, local automated
verification, and a disposable-copy wheel build/install are complete. All
changes remain uncommitted. Real Fedora tools, real Gio Trash behavior, Python
3.10, Ruff, the Rich-enabled current suite, and an sdist/Twine check remain
blocked or unverified.

## Completed

- Read `FINAL_SECURITY_AUDIT.md` and inspected the active `appsweep/` package,
  tests, docs, and Git worktree.
- Disabled permanent personal-file deletion because a mode-0700 quarantine
  owned by the same UID is not a security boundary:
  - `SafetyPolicy.remove_personal_file` raises
    `PERMANENT_PERSONAL_DELETION_DISABLED_REASON` before any rename or unlink.
  - The old quarantine algorithm is now the private
    `_remove_personal_file_quarantine_legacy` method, retained only for
    regression/history and future redesign.
  - `CleanupEngine.execute_plan` rejects `permanent_personal` plans before
    authorization or filesystem work.
  - The CLI no longer offers a permanent-deletion prompt and always builds a
    Trash-only plan.
- Added deterministic same-UID/public-refusal tests, engine refusal tests,
  pre-handoff Gio revalidation, missing-Gio no-fallback, and Gio-failure
  no-fallback tests.
- Made report-only, dry-run, and `--refresh-metadata` semantics explicit:
  - Default DNF preview remains `--cacheonly`.
  - Refresh warning is printed before discovery only when package discovery is
    enabled.
  - The flag is ignored with a warning when packages are not selected.
  - `--dry-run --refresh-metadata` remains allowed and warns.
  - Report-only text distinguishes “No changes were made” from “No cleanup
    actions were performed; DNF metadata may have been refreshed.”
- Updated user-facing notes in analyzer, reporting, UI, and models to
  Trash-only/disabled wording.
- Updated `AGENTS.md`, `README.md`, `ARCHITECTURE.md`, `INTEGRATIONS.md`,
  `RELEASE_CHECKLIST.md`, `TODO.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, and
  `PROGRESS.md`.
- Added `SECURITY.md`, `PUBLISHING.md`, and MIT `LICENSE`; updated
  `pyproject.toml` license metadata and `.gitignore` for historical validation
  evidence.

## Verification recorded

From the repository root (`appsweep/`):

```bash
python3 -m compileall -q appsweep tests
python3 -m unittest discover -v
python3 -m unittest -v tests.test_safety
python3 -m unittest -v tests.test_cleanup_engine
python3 -m unittest -v tests.test_cli
python3 -m unittest -v tests.test_package_flatpak
git diff --check
git status --short --untracked-files=all
```

Results:

- `compileall`: PASS.
- Full suite: 185 tests, 184 passed, 1 Rich-only skip, 0 failures.
- `tests.test_safety`: 39 tests, all pass.
- `tests.test_cleanup_engine`: 12 tests, all pass.
- `tests.test_cli`: 19 tests, all pass.
- `tests.test_package_flatpak`: 21 tests, all pass.
- `git diff --check`: PASS after removing the extra README blank line.
- Rich suite: BLOCKED because the temporary Rich venv is unavailable.
- Ruff: BLOCKED because Ruff is not installed.
- `pytest`: NOT TESTED because it is not installed.
- Final-source wheel: PASS in a disposable copy. Built
  `appsweep-1.0.0-py3-none-any.whl` with pip, inspected 30 entries (only
  `appsweep/` and dist-info; no `appssweep/` or tests), verified MIT license and
  `appsweep = appsweep.cli:main`, installed it into a disposable venv, ran
  `appsweep --version`, completed a report-only smoke scan, and uninstalled it
  cleanly. `build`/`twine` sdist checks remain blocked.

## Intentional exclusions

- `INDEPENDENT_VALIDATION_REPORT.md` is historical validation evidence for the
  older baseline. It was not rewritten and is ignored for publication.
- `Archive.zip` is a gitignored historical local archive and was not rewritten.
- `FINAL_SECURITY_AUDIT.md` lives outside the repository and was not modified.
- Generated caches and `__pycache__` are not source-of-truth text.
- `../taskvault` is unrelated and has not been touched.

## Remaining work

- Review the complete uncommitted diff and confirm only intended files changed.
- Re-run Rich and Ruff when an authorized environment is available.
- Complete the Fedora, Gio, pkexec, real-mount, Python 3.10, and final-wheel
  installation release gates in `TODO.md`, `INTEGRATIONS.md`, and
  `RELEASE_CHECKLIST.md`.
- Do not commit or push without explicit permission. Follow `PUBLISHING.md`
  after approval.

## Safety reminders

- The workspace contains real user files. Inspect before deleting anything.
- Run destructive or race tests only against disposable temporary fixtures as
  an unprivileged user.
- Never run a full destructive cleanup against the host or personal files.
- `real_host_verified` remains `false`.
- Permanent personal-file deletion remains disabled.
