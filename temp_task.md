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
- [x] Added `appssweep/capabilities.py`: read-only PATH detection for DNF,
  DNF5, rpm-ostree, RPM, Flatpak, journalctl, pkexec, and gio; actionable limits;
  explicit mocked/unverified wording.
- [x] Integrated the capability report into `HOST INTEGRATIONS`, JSON export,
  cleanup discovery, and the no-category/report-only paths.
- [x] Added `tests/test_capabilities.py` and
  `tests/test_host_integration_mocks.py`; existing tests now also assert the
  capability section and JSON status. All host-command tests are explicitly
  mocks.
- [x] Added `INTEGRATIONS.md` with an implementation matrix, current sandbox
  status, mocked-coverage inventory, and a detailed manual real-Fedora
  validation checklist.
- [x] Fixed journal usage parsing to accept real `journalctl` short units
  (`1.0G`) as well as IEC units (`1.0GiB`).

## Verification state

- 166 tests total: 165 pass + 1 Rich-only skip without Rich; 166/166 with Rich.
- P1 fail-first evidence: `tests.test_safety` initially ran 26 tests with 2
  failures and 6 errors; after remediation it has 31 passing tests.
- `python3 -m compileall -q appssweep tests`: passes.
- Ruff in the Rich venv: `ruff check appssweep tests` passes. `ruff format
  --check` still reports pre-existing formatting differences across the
  project; formatting the whole tree was intentionally not done to avoid
  unrelated churn.
- Isolated QUICK, CUSTOM, FULL, and `--dry-run` smoke scans: pass.
- Capability smoke: `--categories none --report-only` shows `HOST INTEGRATIONS`,
  lists the available `gio` and the missing Fedora tools, and exits 0. JSON
  contains eight capability records and `real_host_verified: false`.
- JSON/CSV exports: valid, mode `0600`, and report-only runs do not create an
  audit log.
- Remediation smoke in an isolated fixture: custom scan exited 0 with 2
  personal files, 0 cleanup items, valid JSON/CSV, 2 `personal_file` CSV rows,
  and no audit log. QUICK scan with isolated `HOME`/`XDG_STATE_HOME` exited 0,
  printed `Report only`, did not print `Type CLEAN`, and created no audit log.
- Earlier revision: `pip install --force-reinstall --no-deps .` in a clean venv;
  console script reports `appssweep 1.0.0` and completes a quick scan.
- Earlier revision: `pip wheel --no-deps .` built a wheel containing
  `appssweep/audit.py` and the `appssweep = appssweep.cli:main` entry point.
- Final-source packaging re-check is blocked: both available venvs lack
  `setuptools.build_meta`/`build`. Packaging configuration was not changed, but
  this is not recorded as verified for the capability or remediation changes.
- `../taskvault` remains untouched.
- Real Fedora host tools remain unavailable in this Flatpak sandbox; `gio` is
  present but was not used against the real host Trash. No real-host validation
  is claimed. No real secondary filesystem or bind mount was created.
- The remediation is committed on `main`; `INDEPENDENT_VALIDATION_REPORT.md`
  remains untracked as a temporary validation artifact.

## Next move

1. Continue with the `TODO.md` P1 items using the manual checklist in
   `INTEGRATIONS.md`; a real Fedora host and authorized real-mount fixtures are
   still required. Do not push or tag without explicit permission.
2. Do not claim real Fedora or real bind-mount coverage from mock tests or
   simulated mount IDs.
3. Remaining P2 work: per-filesystem free-space reporting, automated wheel
   packaging coverage, audit-log retention, and the project license.
4. Re-run the full suite after any host-driven fix and record the real Fedora
   and tool versions in `PROGRESS.md` and the `INTEGRATIONS.md` sign-off table.

## Independent validation remediation (2026-11-09)

Report: `INDEPENDENT_VALIDATION_REPORT.md` (added temporarily as untracked).
The report baseline is `f96eaf2` with 124 tests; the pre-remediation HEAD was
`3ac4053` with a 138-test suite, and the committed remediation has 166 tests.
Revalidate all findings against the current source rather than copying the
report's stale evidence.

Priority plan:

- **P0:** None confirmed. Do not invent a P0.
- **P1:** Fix BUG-001 cache symlink-swap escape and BUG-002 permanent
  personal-file parent-swap escape with descriptor-relative, no-follow,
  fail-closed operations. Include POT-005 in the final personal-file identity
  check. Add deterministic race-window regressions that fail first.
- **P2:** Fix BUG-003 cross-root hard-link double counting and BUG-004 ignored
  `cancel_check`; bound POT-001 huge selection ranges; handle POT-002
  same-device bind mounts by mount identity; harden POT-003 directory
  replacement and POT-004 wide-directory cancellation.
- **P3:** Reconcile README/ARCHITECTURE/RELEASE_CHECKLIST claims, update
  PROGRESS/TODO/CHANGELOG with exact commands and counts, and record blocked
  host/mount validation honestly.

Order of work:

1. Add fail-first P1 race tests using temporary fixtures and an unprivileged
   user; run them against current code and record the failures.
2. Implement fail-closed descriptor-relative cache traversal/removal and
   permanent personal unlink with final identity checks.
3. Add and fix P2 regression tests incrementally, keeping existing behavior.
4. Run compileall, the full unittest suite, available static checks, and
   report-only CLI smoke checks.
5. Update documentation and review the final diff/status. A single remediation
   commit was later explicitly authorized.

Original constraints: no commit, push, tag, history rewrite, package
installation, or real cleanup. Mount integration remains blocked unless the
environment explicitly permits it; mountinfo unit fixtures are not real
bind-mount evidence.

## Remediation results (final)

- `BUG-001` cache cleanup now anchors an opened target descriptor, traverses
  with `O_DIRECTORY | O_NOFOLLOW`, uses `scandir(fd)` and `dir_fd` stat/unlink/
  rmdir, verifies child and target identities, checks mount IDs, and fails
  closed if the required primitives or mount-boundary verification are
  unavailable.
- `BUG-002` permanent personal unlink now opens the approved root and parent
  chain by descriptor, verifies the parent path against its descriptor, checks
  the selected file against the full snapshot through that parent, and unlinks
  with `dir_fd`. A swap or changed identity is refused.
- `BUG-003` hard-link accounting is global across all scan roots and loose-file
  roots.
- `BUG-004` convenience `cancel_check` callbacks are consulted, including
  during incremental wide-directory enumeration.
- `POT-001` range endpoints/span are validated against available choices before
  set construction, and integer parsing is bounded.
- `POT-002` every exact nested mount point is pruned; explicitly selected nested
  roots are still scanned. Cache traversal rejects mount-ID boundaries,
  including same-device bind mounts, via `/proc/self/fdinfo`.
- `POT-003`/`POT-004` scanner queued directories are reopened with `O_NOFOLLOW`,
  identity-checked, and enumerated incrementally with descriptor-relative entry
  stats.
- `POT-005` the final personal-file check includes mtime (plus owner, device,
  inode, and size).
- Regression tests cover cache top/nested/target/file-parent swaps, personal
  symlink and real-directory parent replacement, selected-file identity/mtime
  changes, cross-root hard links, callbacks, huge ranges, nested mounts,
  scanner directory swaps, wide-directory cancellation, and unavailable
  mount-ID verification.
- Final verification: 166 tests total (165 pass + 1 Rich-only skip without
  Rich; 166/166 with Rich), compileall pass, Ruff pass, report-only custom and
  QUICK smoke checks pass, no audit log created, no real cleanup performed.
- Documentation was reconciled in `PROGRESS.md`, `TODO.md`,
  `RELEASE_CHECKLIST.md`, `CHANGELOG.md`, `README.md`, `ARCHITECTURE.md`,
  `INTEGRATIONS.md`, `AGENTS.md`, `CONTRIBUTING.md`, and this file.
- A single remediation commit was made on `main`; no push, tag, history rewrite,
  package installation, real cleanup, or real mount was performed.
  `INDEPENDENT_VALIDATION_REPORT.md` remains untracked.

## Safety reminders

- The workspace contains real user files. Inspect before deleting anything.
- Test only inside temporary directories.
- Existing cleanup paths must remain explicitly confirmed and allowlisted.
- Do not publish, push, or create a GitHub release without permission.
