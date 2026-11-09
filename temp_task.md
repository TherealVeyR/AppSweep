# Current Task Cache

Last updated: 2026-11-09

## Original next steps before this steering directive

I was finishing the AppSweep release-candidate review:

1. Re-run compileall, the full unittest suite, and the Rich suite after the
   latest safety changes.
2. Run a final Ruff pass and only fix real issues.
3. Re-run QUICK/CUSTOM/FULL smoke scans and JSON/CSV export checks.
4. Re-run `pip install .` and verify the installed console script.
5. Confirm `/home/Echo/Documents/Default Project/taskvault` remains untouched.
6. Update `PROGRESS.md`, `TODO.md`, `AGENTS.md`, and this file with the final
   verified state.
7. Continue the release review without starting unrelated features.

At that point the verified baseline was 107 tests (106 pass + 1 Rich-only skip
without Rich; 107/107 with Rich), Ruff clean, and no known code bugs in tested
paths. Real Fedora host-tool validation was still blocked by the Flatpak
sandbox.

## Completed under the steering directive

- [x] Inspected the project; no Git repository existed and no accidental
  project-name misspelling was present in user-facing text.
- [x] Initialized Git in `appssweep/` on `main` with a local identity and the
  conventional root commit `feat: establish AppSweep release candidate`
  (`dee1126`).
- [x] Committed the FULL-scan, Trash-preview, and regression-test milestone as
  `feat: expand full scan coverage and trash previews` (`9a98912`).
- [x] Recorded wheel-build and host-tool evidence as
  `docs: record wheel build and host tool limits` (`d5591f9`).
- [x] Committed the edge-case coverage as
  `test: cover CLI, export, and terminal edge cases` (`c7aec50`).
- [x] Expanded `.gitignore` and removed generated build/test artifacts before
  staging.
- [x] Added `CHANGELOG.md`, `CONTRIBUTING.md`, and `RELEASE_CHECKLIST.md`.
- [x] Updated `AGENTS.md`, `PROGRESS.md`, `TODO.md`, `ARCHITECTURE.md`, and
  `README.md` for audit logging, dry-run, Git, release gates, and host limits.
- [x] Added private append-only cleanup audit logging (`appssweep/audit.py`)
  with started/finished/refused records, `0700`/`0600` privacy, symlink,
  hard-link, FIFO, and unsafe-directory refusal, plus safe write-failure
  warnings.
- [x] Added `--dry-run` as an explicit alias for `--report-only`.
- [x] Extended FULL-scan discovery to separately mounted local `/opt`, `/etc`,
  `/root`, `/srv`, and disk-backed `/tmp`, plus read-only `/usr` images on
  immutable Fedora systems.
- [x] Added Trash previews with size, top-level count, and up to eight example
  contents.
- [x] Added regression coverage for audit privacy/failures, dry-run, full-root
  discovery, Trash previews, hard links, and duplicate personal paths.
- [x] Added edge-case coverage for `--refresh-metadata` wiring, export paths
  with spaces or a read-only parent, 64-column rendering, and Rich fallback.

## Verification state

- 124 tests total: 123 pass + 1 Rich-only skip without Rich; 124/124 with Rich.
- `python3 -m compileall -q appssweep tests`: passes.
- Ruff in the Rich venv: passes.
- Isolated QUICK, CUSTOM, FULL, and `--dry-run` smoke scans: pass.
- JSON/CSV exports: valid, mode `0600`, and report-only runs do not create an
  audit log.
- `pip install --force-reinstall --no-deps .` in a clean venv: console script
  reports `appssweep 1.0.0` and completes a quick scan.
- `pip wheel --no-deps .` builds a wheel containing `appssweep/audit.py` and the
  `appssweep = appssweep.cli:main` entry point.
- `../taskvault` remains untouched.
- Real Fedora host tools remain unavailable in this Flatpak sandbox; `gio` is
  present but was not used against the real host Trash.

## Next move

1. The working tree is clean after the edge-case test commit; continue from the
   remaining `TODO.md` P1 host-validation items.
2. Do not claim real Fedora coverage from mock tests.
3. Remaining P2 work: per-filesystem free-space reporting, automated wheel
   packaging coverage, audit-log retention, and the project license.
4. Re-run the full suite after any host-driven fix and record the real Fedora
   and tool versions in `PROGRESS.md`.

## Safety reminders

- The workspace contains real user files. Inspect before deleting anything.
- Test only inside temporary directories.
- Existing cleanup paths must remain explicitly confirmed and allowlisted.
- Do not publish, push, or create a GitHub release without permission.
