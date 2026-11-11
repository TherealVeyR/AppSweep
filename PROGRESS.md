# AppSweep Progress

Last updated: 2026-11-11
Status: **final security remediation, publication files, and the project-name
rename are implemented in the working tree and not committed. The suite is 185
tests (184 pass, 1 Rich-only skip). Permanent personal-file deletion is disabled
and fails closed; personal files are Trash-only. A disposable-copy wheel build,
archive inspection, isolated install/uninstall, console-script check, and
report-only smoke scan passed. Real-Fedora host-tool validation, Python 3.10
runtime, real mount fixtures, and real-Gio Trash validation remain unverified.**

## Current state

AppSweep is a working local Fedora storage analyzer and cleanup assistant.
It scans read-only, reports storage use and cleanup opportunities, lists the 50
largest personal files, supports numbered selection, requires an explicit
`CLEAN` confirmation, and moves selected personal files to Trash only.

`../taskvault` is unrelated and has not been touched.

The older independent validation findings `BUG-001` through `BUG-004` and the
report's `POT-001` through `POT-005` are remediated and committed on `main`.
That report was evaluated against `f96eaf2` with 124 tests; the pre-remediation
`HEAD` was `3ac4053`, and that remediation grew the suite to 166 tests.

A later follow-up review identified `BUG-005`, `BUG-006`, and `BUG-007`, plus a
ctime hardening review. The final security audit then reproduced the same-UID
quarantine-entry replacement. Permanent personal-file deletion is now disabled
because a mode-0700 quarantine owned by the same UID is not a security boundary;
the public API, cleanup engine, and CLI fail closed. The suite is now 185 tests.
The audit's labels `POT-001` (Gio), `POT-002` (metadata refresh), `POT-003`
(audit retention), and `POT-004` (legacy quarantine recovery) are recorded
separately from the older untracked report's same-numbered findings.
`INDEPENDENT_VALIDATION_REPORT.md` still covers the older baseline and remains
untracked and unmodified.

## Project rename (uncommitted)

- The project directory, Python package, source launcher, console command, and
  cleanup audit state directory now use the `appsweep` spelling; the
  user-facing product name remains **AppSweep**.
- Imports, tests, packaging metadata, examples, and active documentation were
  updated together. The parent workspace `AGENTS.md` also points at
  `appsweep/`.
- `INDEPENDENT_VALIDATION_REPORT.md` and `Archive.zip` are historical artifacts
  and were not rewritten; generated caches were left alone.
- Verification after the rename and final remediation:
  `python3 -m compileall -q appsweep tests` passed; the 185-test suite returned
  184 passes and one Rich-only skip; `python3 -m appsweep --version` and
  `./bin/appsweep --version` both report `appsweep 1.0.0`.
- Existing audit state is not migrated automatically to the new state path.

## Follow-up validation remediation (uncommitted)

### BUG-005 — hard-link alias omitted from personal results

- `StorageScanner` now separates global storage accounting from personal-file
  eligibility. A hard-link alias whose inode storage was already counted under
  another scan root is still evaluated as a personal candidate.
- Hard-link storage is counted once per `(st_dev, st_ino)` across directory
  roots and loose-file roots.
- Multiple eligible aliases of one inode use one deterministic Top 50
  representative: the largest snapshot first, then the lexicographically
  smaller path. Results no longer depend on scan-root order.
- The bounded Top 50 candidate map keeps one entry per inode. Replacing a
  superseded alias cannot consume a slot or evict a different inode's eligible
  file when two files have the same size.
- Regression coverage:
  `tests/test_scanner.py` checks non-personal-root-first and reverse root order,
  plus deterministic alias selection across two personal roots and a same-size
  alias-update case with a forced directory order.
- Fail-first evidence: the three new scanner regressions run against `HEAD`
  (`4502ef7`) in a disposable copy failed 3/3. The non-personal-root-first case
  returned 0 top files instead of 1, the representative stayed scan-order
  dependent, and the same-size case returned `['middle-other.bin',
  'z-alias.bin']` instead of the expected `['a-alias.bin',
  'middle-other.bin']`. All three pass against the current scanner.

### Final security remediation — same-UID quarantine boundary

- The final audit deterministically reproduced a same-UID process replacing the
  entry inside the mode-0700 quarantine after validation but before `unlink`.
  The old flow could delete the replacement, report the selected file's size,
  and leave the selected inode in quarantine.
- `SafetyPolicy.remove_personal_file` now raises `SafetyError` with
  `PERMANENT_PERSONAL_DELETION_DISABLED_REASON` before any rename or unlink.
  The historical quarantine algorithm is retained only as the private
  `_remove_personal_file_quarantine_legacy` method for regression/history and
  future redesign; application code never calls it.
- `CleanupEngine.execute_plan` rejects `plan.permanent_personal` with
  `PermissionError` before authorization coverage, snapshot validation, or any
  filesystem operation.
- The CLI no longer offers a `DELETE PERMANENTLY` prompt, always builds a
  Trash-only plan, and prints the disabled reason when personal files are
  present. Missing or failing Gio never triggers a direct-unlink fallback.
- Regression coverage now includes the public refusal path (no `os.rename`, no
  `os.unlink`, no legacy call), a same-UID substitution scenario that proves
  the public method refuses before a destructive syscall, the engine's
  permanent-plan refusal even with a valid authorization bit, pre-handoff Gio
  revalidation, missing-Gio no-fallback, and Gio-failure no-fallback.
- The legacy quarantine tests still exercise the private primitive, including
  visible-path, parent-directory, symlink, directory, and final-unlink
  replacement. They are labelled as legacy tests and do not describe a
  user-reachable operation.
- The final source still has no genuine separate privilege boundary for
  permanent personal deletion. Re-enabling it requires a reviewed helper or a
  design that never unlinks a same-UID-replaceable quarantine entry.

### BUG-007 — audit directory could remain `0755` or `0711`

- `appsweep/audit.py` now opens the audit directory by descriptor with
  `O_DIRECTORY | O_NOFOLLOW`, verifies ownership and that it is not group/world
  writable, and enforces exactly `0700`. Existing `0755` and `0711` directories
  are tightened; `0777`-style directories remain refused.
- The audit file is opened relative to the verified directory descriptor with
  `O_NOFOLLOW`, forced to `0600`, and checked for regular-file type, ownership,
  and hard-link count.
- Regression coverage: `tests/test_audit.py` verifies existing `0755` and
  `0711` directories become `0700`, log files stay `0600`, and the existing
  writable-directory, symlink, and hard-link refusals still pass.

### ctime review

- `FileSnapshot` and `FileIdentity` now carry `ctime_ns`. Scanner snapshots and
  test helpers populate it.
- Preflight validation and the legacy quarantine's pre-rename check compare
  ctime, so a same-size in-place edit that restores the original mtime is
  rejected before any move.
- The post-quarantine check intentionally excludes ctime: the quarantine rename
  itself updates the inode change time. It still checks device, inode, owner,
  size, and mtime. This limitation is documented for the private legacy
  primitive only; permanent deletion is not user-reachable.
- Regression coverage: `tests/test_safety.py` performs a same-size overwrite,
  restores mtime, and verifies both snapshot validation and the private legacy
  primitive refuse it. The public API refuses even earlier.

### Final-audit potential findings

- **POT-001 (Gio Trash pathname race): accepted limitation, not fixed.**
  Moving a personal file to Trash still calls `gio trash -- <path>` after
  validation, so a final-component swap during that handoff is not closed. It
  is recoverable rather than permanently destructive, and there is no
  permanent-deletion fallback. Gio is present in this environment but no real
  desktop Trash operation was performed, so this remains **BLOCKED** for
  real-host validation. Do not claim it is race-free.
- **POT-002 (metadata refresh): explicit and conspicuous.** The default DNF
  preview uses `--cacheonly`. `--refresh-metadata` allows a retry without it
  only when package discovery is enabled; AppSweep warns before discovery,
  ignores the flag with an explanation otherwise, and changes the report-only
  text to distinguish “no cleanup actions” from “metadata may have been
  refreshed.” `--dry-run --refresh-metadata` remains an explicit side effect
  with the same warning. Real DNF metadata writes remain **BLOCKED** because
  DNF is absent.
- **POT-003 (audit retention): no automatic deletion.** The audit log remains
  append-only with no built-in rotation or retention limit, so it cannot
  silently delete audit history. Manual archival/removal is documented. This is
  a deliberate retention policy, not a hidden cleanup path.
- **POT-004 (legacy quarantine recovery): not user-reachable.** The product no
  longer creates quarantine directories because permanent deletion is
  disabled. The private legacy primitive and its restoration tests remain for
  future redesign; any future recovery workflow must inspect and restore only
  after identity validation and must never automatically purge leftovers.

### Final-audit finding disposition

| Finding | Status | Evidence |
| --- | --- | --- |
| Confirmed same-UID quarantine-entry replacement | **Closed by fail-closed refusal** | Public `remove_personal_file` raises before rename/unlink; engine rejects permanent plans; CLI offers no permanent prompt. |
| Previous HIGH visible-path/parent replacement | **Fixed for the cross-UID/visible-path threat model** | Descriptor-relative cache and legacy personal tests; permanent deletion is disabled, so no universal inode-identity guarantee is claimed. |
| Previous MEDIUM hard-link alias omission | **Fixed** | Scanner global inode accounting plus per-path personal eligibility; deterministic alias tests. |
| Previous LOW audit-directory privacy | **Fixed** | Owned `0755`/`0711` directories tightened to `0700`; group/world-writable, symlink, and hard-link paths refused. |
| Potential ctime same-size restored-mtime edit | **Fixed** | Snapshot includes `ctime_ns`; public deletion refuses even earlier; private legacy tests reject the edit. |
| Earlier cancellation callback regression | **Fixed** | Convenience callback and wide-directory cancellation tests. |
| Earlier cache replacement/symlink escape | **Fixed for tested synthetic scenarios** | Descriptor-relative no-follow traversal, inode and mount-ID checks, deterministic replacement tests. Real mounts remain blocked. |
| POT-001 Gio Trash final-component race | **Accepted recoverable limitation; host blocked** | Pre-handoff revalidation and no-fallback tests; no claim of race-free behavior. |
| POT-002 DNF metadata refresh during report generation | **Implemented and tested; real DNF blocked** | Cache-only default, opt-in warning, ignored-flag warning, distinct report-only wording, four combos tested. |
| POT-003 audit-log retention | **Documented; no automatic deletion** | Append-only log and manual archival documented; no silent truncation. |
| POT-004 interrupted quarantine recovery | **Moot for the product; legacy only** | Permanent deletion is disabled, so the product creates no quarantine directories. Future redesign must not auto-purge. |
| P1.1 same-UID release boundary | **Decided** | Permanent personal-file deletion disabled; no claim of same-UID protection. |
| P1.2 do not mark HIGH unconditionally fixed | **Done** | Docs state the cross-UID/visible-path scope and the same-UID limitation. |
| P1.3 Gio threat-model decision | **Accepted recoverable limitation** | `SECURITY.md`, `README.md`, `ARCHITECTURE.md`, and `INTEGRATIONS.md` state it is not race-free. |
| P2.1 clarify metadata refresh/dry-run | **Done** | CLI warnings, final wording, and tests for default, refresh, dry-run, and both. |
| P2.2 same-UID regression | **Done** | `test_same_uid_quarantine_replacement_cannot_reach_a_public_unlink` and engine refusal tests. |
| P2.3 real Fedora tests | **BLOCKED** | No Fedora host, real DNF/RPM/rpm-ostree/Flatpak/pkexec/Gio/desktop, or real mounts available. |
| P2.4 reconcile package rename in Git | **Prepared, not committed by instruction** | `PUBLISHING.md` preflight and `git add -A` migration review; working tree remains dirty. |
| P2.5 quarantine inspection/recovery | **Future redesign only** | Product path disabled; private legacy primitive remains out of reach. |
| P3.1 audit retention policy | **Documented** | Append-only behavior and manual archival; no automatic deletion. |
| P3.2 Ruff/static typing | **BLOCKED** | Tools not installed; not claimed passing. |
| P3.3 Python 3.10/CI | **Prepared, runtime BLOCKED** | GitHub Actions matrix added; Python 3.10 runtime not available here. |

### Final verification recorded on 2026-11-11

- `python3 -m compileall -q appsweep tests`: pass.
- `python3 -m unittest discover -v`: 185 tests, 184 pass, 1 Rich-only skip, 0
  failures.
- Focused suites after the final edits:
  `tests.test_safety` 39 tests pass; `tests.test_cleanup_engine` 12 tests pass;
  `tests.test_cli` 19 tests pass; `tests.test_package_flatpak` 21 tests pass.
- CLI semantics smoke against a synthetic `keep.txt` with an isolated home:
  default report-only printed `Report only.` / `No changes were made.`;
  `--refresh-metadata` with `--categories none` warned that the flag had no
  effect and still printed `No changes were made.`;
  `--categories packages --dry-run --refresh-metadata` and the same combination
  with `--report-only` warned before discovery and printed `No cleanup actions
  were performed. DNF repository metadata may have been refreshed.` The fixture
  hash did not change and no audit log was created.
- Rich venv `/tmp/opencode/appsweep-rich-venv/bin/python -m unittest discover
  -q`: **BLOCKED** for the current 185-test suite because the temporary venv is
  no longer present. The previous 175-test Rich-enabled run passed 175/175.
- `ruff check appsweep tests`: **BLOCKED** for the current tree because Ruff is
  not installed. The previous 175-test tree passed Ruff before the final
  scanner regression test was added.
- `git diff --check`: pass after removing an extra blank line at end of file.
- `pytest`: not installed, so not run.
- Environment is Python 3.14.9. No Fedora host tools, real Gio Trash operation,
  pkexec run, real secondary filesystem, real same-device bind mount, or Python
  3.10 runtime run was performed. `ast.parse(..., feature_version=(3, 10))`
  passes for all `appsweep/*.py` files, but a syntax parse is not a runtime
  test. A disposable-copy wheel build, archive inspection, isolated install,
  console-script check, and report-only smoke scan passed; see "Packaging
  verification" below.

## Completed work

### Core scanning and analysis

- [x] Shared data models for files, cleanup items, risk groups, actions,
  scans, reports, authorization, and execution results.
- [x] Extension-based file classification with conservative descriptions.
- [x] Mount discovery from `/proc/self/mountinfo`, local/network/virtual
  classification, mount-boundary checks, and filesystem capacity reporting.
- [x] Main scanner based on `os.scandir` with:
  - [x] descriptor-relative directory traversal using
    `O_DIRECTORY | O_NOFOLLOW` and `fstat` identity checks
  - [x] incremental enumeration so cancellation can stop inside a wide
    directory instead of materializing every entry first
  - [x] path/device/inode rechecks when a queued directory is reopened
  - [x] boundary pruning for every nested mount point, including same-device
    bind mounts; explicitly selected nested roots are still scanned
  - [x] symlink reporting without following
  - [x] global hardlink storage accounting across every scan root and loose-file
    root, with personal eligibility still evaluated for every alias
  - [x] permission-error tracking
  - [x] skipped-path reporting
  - [x] cancellation with partial results, including convenience
    `cancel_check` callbacks
  - [x] bounded top-N personal-file candidate map
  - [x] personal-file filtering
  - [x] progress callback
- [x] Targeted directory measurement for cleanup discovery.
- [x] QUICK, FULL, and CUSTOM scan setup.
- [x] Quick scan covers common personal folders; full scan follows the live
  installation while skipping virtual, network, and external mounts; custom
  scan requires explicit `--path` roots.
- [x] FULL scan includes separately mounted local `/opt`, `/etc`, `/root`,
  `/srv`, and disk-backed `/tmp`, plus a read-only `/usr` image on immutable
  Fedora systems. Nested mount points are pruned from a parent traversal and
  scanned separately only when they are also explicit scan roots.
- [x] Flatpak sandbox detection and limitation reporting.

### Cleanup discovery and reporting

- [x] DNF/DNF5 package-cache, autoremove-preview, and old-kernel discovery.
- [x] rpm-ostree metadata-only cleanup discovery.
- [x] Centralized, read-only PATH capability detection for DNF, DNF5,
  rpm-ostree, RPM, Flatpak, journalctl, pkexec, and gio. The terminal shows a
  `HOST INTEGRATIONS` section, and JSON records `real_host_verified: false`
  with explicit mocked-coverage wording.
- [x] Missing optional tools degrade independently: scanning and unrelated
  cleanup continue, and each missing tool has an actionable limit. No shell or
  host command is executed during capability detection.
- [x] Corrected journal size parsing for real `journalctl` short units such as
  `1.0G` while retaining IEC units such as `1.0GiB`.
- [x] Flatpak unused-runtime discovery with conservative estimate label.
- [x] User and app cache discovery, including Flatpak app caches.
- [x] Journal and rotated-log discovery.
- [x] Trash analysis with size, top-level count, and up to eight example
  contents shown before emptying.
- [x] Trash emptying requires explicit selection and the normal `CLEAN`
  confirmation; missing `gio` leaves Trash report-only with no direct-delete
  fallback.
- [x] Report-only findings for data that must not be auto-cleaned.
- [x] Cleanup item numbering by group, size, and title.
- [x] Overlap detection and personal-file/category de-duplication.
- [x] Direct overlap-key tracking so keys containing `]` are still disabled
  safely, plus analyzer regression tests for overlap, de-duplication, and
  report-only merging.
- [x] Separate top-50 personal files table and selection map.
- [x] Selection parser: `1`, `1,3,5`, `1-10`, mixed syntax, `all`, `none`,
  `cancel`, `!` exclusions, and cleanup group words.
- [x] Invalid, reversed, disabled, and mixed cancel/none selections rejected.
- [x] Range endpoints and span are validated against the available choices
  before constructing sets; huge ranges and absurdly long numbers are rejected
  predictably.

### Safety and execution

- [x] Safety policy with protected system paths, trees, home data, cache roots,
  and Trash roots.
- [x] Explicit cache cleanup cannot bypass `.ssh`, `.gnupg`, `.local`, or other
  protected home trees. The cache-root override is limited to
  `~/.var/app/<app>/cache`; Flatpak app data and configuration remain protected.
- [x] Snapshot revalidation by path, device, inode, size, mtime, ctime, owner,
  and group.
- [x] Descriptor-relative cache clearing: stable anchor/parent descriptors,
  `O_DIRECTORY | O_NOFOLLOW`, `dir_fd` stat/unlink/rmdir, child inode checks,
  final path-to-descriptor identity checks, and mount-ID boundary checks. It
  fails closed when those primitives or `/proc/self/fdinfo` are unavailable.
- [x] Public permanent personal-file deletion fails closed: the safety API
  raises before any rename or unlink, and the cleanup engine rejects a
  permanent plan before authorization or filesystem work.
- [x] Historical private-quarantine algorithm retained as
  `_remove_personal_file_quarantine_legacy` for regression/history and future
  redesign only; application code never calls it.
- [x] Deterministic race regressions replace cache targets, nested cache
  directories, personal parents, and selected files at controlled check/use
  windows. Legacy quarantine regressions cover final-unlink, pre-quarantine,
  symlink, directory, and same-size/restored-mtime cases. Public same-UID
  substitution and engine refusal tests prove no destructive syscall is
  reached.
- [x] Same-device bind mounts inside cache targets are rejected by mount ID,
  not only by device number.
- [x] Trash-only personal handling through `gio trash --`; pre-handoff
  revalidation, no fallback when Gio is missing, and no fallback when Gio
  fails.
- [x] Allowlisted command-shape validation for DNF, Flatpak, journalctl,
  rpm-ostree, and gio.
- [x] No `shell=True`; subprocess runner uses argument lists.
- [x] Per-command `pkexec` support for system actions; app refuses euid 0.
- [x] Cancellation disables cleanup during discovery.
- [x] Report-only/dry-run/refresh-metadata semantics distinguish “no cleanup
  actions” from possible DNF metadata refresh, with a conspicuous warning and
  an ignored-flag warning when package discovery is disabled.
- [x] Private append-only cleanup audit log in the user state directory:
  `0700` directory (existing `0755`/`0711` directories tightened; writable
  shared directories refused), `0600` JSON-lines file, descriptor-relative
  symlink/hard-link refusal, and started/finished/refused records.
- [x] Audit-log failures are surfaced as warnings without hiding a cleanup
  result.
- [x] `--dry-run` alias for `--report-only`; neither mode offers cleanup.

### Interface and export

- [x] Polished ANSI terminal UI with optional Rich auto-detection.
- [x] Complete Rich fallback behavior when Rich is absent or fails.
- [x] Storage summary, grouped cleanup details, top-50 table, notes, selection
  summaries, and combined confirmation.
- [x] Terminal control-character escaping for filesystem-derived strings.
- [x] JSON export with scan, cleanup, personal files, warnings, overlaps,
  findings, and package/Flatpak details.
- [x] Single CSV export with `record_type` rows for personal files, cleanup
  items, and report-only findings.
- [x] CSV spreadsheet-formula injection mitigation.
- [x] Atomic, private (`0600`) report writes.
- [x] Non-TTY report-only default; `--interactive` override.
- [x] Interactive prompts require both stdin and stdout to be terminals unless
  `--interactive` is supplied, so redirected output cannot hide a prompt.
- [x] Exit codes for success, failure, usage error, and cancellation.

### Packaging and documentation

- [x] Source launcher `bin/appsweep`.
- [x] `pyproject.toml` with console-script entry point and optional Rich extra.
- [x] `requirements.txt` documenting zero required dependencies.
- [x] `README.md` with installation, usage, safety, export, testing, and
  limitations.
- [x] `.gitignore` covering Python artifacts, virtual environments, build
  output, test/lint caches, secrets, IDE files, generated reports, and audit
  logs.
- [x] `AGENTS.md`, `PROGRESS.md`, `TODO.md`, `ARCHITECTURE.md`, and
  `temp_task.md`.
- [x] `CHANGELOG.md`, `CONTRIBUTING.md`, and `RELEASE_CHECKLIST.md`.
- [x] `INTEGRATIONS.md` with the implementation matrix, mocked-coverage
  inventory, current host-tool status, and a detailed manual real-Fedora
  validation checklist. It does not claim real-host results.
- [x] `SECURITY.md` with the threat model, same-UID limitations, Gio
  limitation, and private reporting guidance.
- [x] `PUBLISHING.md` with the detailed pre-publication, Git, GitHub, wheel,
  and release guide.
- [x] `LICENSE` with the MIT license; `pyproject.toml` declares the license
  and includes the license file.
- [x] `.gitattributes` for LF-normalized text and binary fixtures.
- [x] `.github/workflows/tests.yml` compile/unittest matrix for Python 3.10
  through 3.14. It has not been executed on GitHub in this environment.
- [x] Git repository initialized in the AppSweep project directory with a
  local commit identity and conventional, verified milestone commits.

## Verification evidence

### Automated tests

Command:

```bash
python3 -m compileall -q appsweep tests
python3 -m unittest discover -v
```

Result on 2026-11-11, after the final security remediation:

- **185 tests total**
- **184 passed, 1 skipped** without Rich
- Current Rich run: **BLOCKED** (temporary venv unavailable); previous
  175-test run passed 175/175
- **0 failures**
- `python3 -m compileall -q appsweep tests`: pass
- `ruff check appsweep tests`: **BLOCKED** (Ruff not installed); previous
  175-test tree passed
- `git diff --check`: pass
- `pytest`: not installed, not run

The safety module now has 39 tests, the scanner module 24, and the audit module
6. The earlier remediation's fail-first evidence remains historical: before the
descriptor-relative implementation, `tests.test_safety` ran 26 tests with 2
failures and 6 errors; after that remediation it had 31 passing tests and the
full suite was 166. The follow-up changes added scanner, audit, ctime,
permanent-deletion refusal, Gio handoff, metadata-refresh, and publication
coverage; the suite is now 185.

The suite covers selection parsing (including oversized ranges), filesystem
measurement, descriptor-relative scanner traversal, directory-swap races,
symlinks, permissions, mount boundaries, global hardlink accounting,
cancellation callbacks and wide-directory cancellation, full-mode root
discovery, safety/authorization, descriptor-relative cache cleanup, public
permanent-deletion refusal, legacy quarantine regressions, Gio handoff
revalidation and no-fallback behavior, cache override protection, package and
Flatpak detection, runner safety, cleanup engine behavior, reporting/export,
CLI flows, report-only/dry-run/refresh-metadata semantics, CSV formula
injection, analyzer overlap handling, exact top-50 display/selection numbering,
cleanup audit-log privacy and refusal paths, Trash content previews,
narrow-terminal rendering, Rich failure fallback, the `--dry-run` alias, and
read-only capability detection. Mocked integration tests cover missing
package/Flatpak tools, rpm-ostree metadata, RPM old-kernel selection with
running-kernel exclusion, journal usage parsing, journalctl vacuum discovery,
and the no-journalctl report-only fallback. These are explicitly mocks, not
real Fedora validation.

### Manual smoke tests

- [x] `./bin/appsweep --mode quick ... --report-only --json ... --csv ...`
  against an isolated temporary home: exit 0, correct ranking, valid JSON/CSV.
- [x] `./bin/appsweep --mode custom --path ...`: exit 0, correct custom root
  and file.
- [x] `./bin/appsweep --mode full` in the Flatpak sandbox: exit 0, correct
  sandbox limitation, isolated home scanned.
- [x] Earlier revision: `python3 -m unittest discover` in a venv with Rich:
  all 175 tests passed. The current 185-test Rich run is blocked because the
  temporary venv is unavailable.
- [x] Earlier revision: `ruff check appsweep tests` in the Rich venv passed.
  The current Ruff check is blocked because Ruff is not installed.
- [x] Independent-validation custom smoke in an isolated fixture:
  `python3 bin/appsweep --mode custom --path <fixture> --min-size 0
  --categories none --report-only --no-color --json <report.json>
  --csv <report.csv>` exited 0; JSON contained 2 personal files and 0 cleanup
  items; CSV contained 2 `personal_file` rows; no cleanup audit log was
  created.
- [x] Independent-validation QUICK smoke with an isolated `HOME` and
  `XDG_STATE_HOME`: exit 0, the output said `Report only`, it did not contain
  `Type CLEAN`, and no cleanup audit log was created.
- [x] Earlier revision: `pip install .` in a clean venv; console script reports
  `appsweep 1.0.0` and completes a quick scan.
- [x] Earlier revision: `pip wheel --no-deps .` built
  `appsweep-1.0.0-py3-none-any.whl`; inspection confirmed
  `appsweep/audit.py` and the `appsweep = appsweep.cli:main` console entry
  point.
- [x] Final-source wheel/install/uninstall re-verification after the capability,
  independent-validation, and final security changes: passed in a disposable
  copy. The wheel contains `appsweep/`, the MIT license, and the
  `appsweep = appsweep.cli:main` entry point; an isolated install reports
  `appsweep 1.0.0`, completes a report-only scan, and uninstalls cleanly. See
  "Packaging verification" above. `build`/`twine` sdist checks remain blocked.
- [x] Rich manual rendering check: `supports_rich() == True`, banner and storage
  content render without error.
- [x] Audit-log tests verify the `0600` file, `0700` directory, tightening of
  existing `0755`/`0711` directories, JSON-lines records, symlink refusal,
  hard-link refusal, and unsafe-directory refusal.
- [x] Interactive CLI test verifies started/finished audit records after a
  confirmed temporary cache cleanup.
- [x] CLI test verifies an unsafe audit-log directory produces a warning while
  the confirmed temporary cache cleanup still completes.
- [x] Cleanup engine test rejects duplicate personal-file paths in a plan.
- [x] Trash tests verify size, top-level count, example contents, and the
  no-`gio` report-only path.
- [x] CLI tests verify `--refresh-metadata` reaches discovery only with package
  discovery enabled, is ignored with a warning otherwise, and that
  report-only/dry-run plus refresh use the correct warning and final wording.
- [x] CLI test verifies interactive personal-file selection never offers
  `DELETE PERMANENTLY` or a permanent-deletion prompt.
- [x] Export tests cover a missing parent, spaces in the report path, and a
  read-only parent directory with a clear `ExportError`.
- [x] UI tests cover 64-column rendering with a long unbroken name and fallback
  to the plain stream when the Rich console raises.
- [x] Capability smoke test in an isolated home with `--categories none`
  displayed `HOST INTEGRATIONS`, reported available `gio`, listed the absent
  DNF/DNF5/rpm-ostree/RPM/Flatpak/journalctl/pkexec tools, and still exited 0.
- [x] The capability smoke JSON contained eight tool records, actionable
  warnings, `real_host_verified: false`, and the explicit mocked/unverified
  wording. No host tool was executed for detection.
- [x] New tests verify short journal units (`1.0G`) and IEC units (`1.0GiB`).

### Packaging verification (disposable copy, uncommitted working tree)

The final source was copied to `/tmp/opencode/appsweep-final-src` while excluding
`.git`, the historical validation report, archives, caches, and build output.
System Python has no `pip`, so a disposable `venv --system-site-packages` was
created and its pip built the wheel without network access:

```bash
python3 -m venv --system-site-packages /tmp/opencode/appsweep-final-venv
/tmp/opencode/appsweep-final-venv/bin/python -m pip wheel \
  --no-deps --no-build-isolation \
  --wheel-dir /tmp/opencode/appsweep-final-dist \
  /tmp/opencode/appsweep-final-src
```

Result:

- `appsweep-1.0.0-py3-none-any.whl` built successfully
  (97,932 bytes, SHA-256
  `7cd2c69905d67ae5dccf0452a70c190adeb1beddfcab7bcda001a218d63549ab`).
- 30 archive entries; top-level paths are `appsweep/` and
  `appsweep-1.0.0.dist-info/`.
- No `appssweep/`, `tests/`, report, audit, or cache entries.
- `METADATA` declares `Name: appsweep`, `Version: 1.0.0`,
  `Requires-Python: >=3.10`, `License-Expression: MIT`, and
  `License-File: LICENSE`.
- `entry_points.txt` contains `appsweep = appsweep.cli:main`.
- License is packaged at `appsweep-1.0.0.dist-info/licenses/LICENSE`.
- A disposable `venv --system-site-packages` installed the wheel with
  `pip install --no-deps --force-reinstall`; `appsweep --version` printed
  `appsweep 1.0.0`, and the imported package resolved inside the venv.
- A report-only custom scan against a synthetic `keep.txt` exited 0, printed
  `Report only` and `No changes were made.`, wrote JSON, left the fixture
  unchanged, and created no audit log.
- `pip uninstall -y appsweep` removed both the console script and the importable
  package; the venv no longer resolved `appsweep`.
- The `build` frontend and `twine` are still unavailable, so no sdist or
  Twine metadata check was run. This is not a real-Fedora packaging test.

## Known limitations and unverified areas

- The sandbox lacks real `dnf`, `dnf5`, `rpm-ostree`, `flatpak`, `journalctl`,
  `rpm`, and `pkexec`; `gio` 2.88.3 is present but was not used against the real
  host Trash during development. Host integrations are tested with mocks and
  command-shape validation only. See `INTEGRATIONS.md` for the exact matrix and
  manual checklist.
- Full host filesystem scanning and real package-manager cleanup have not been
  run on a full Fedora host in this environment.
- The capability report intentionally keeps `real_host_verified: false` until
  a person completes the real-Fedora checklist. Mocked tests cannot change that
  status.
- Flatpak 1.18 has no supported non-interactive unused-ref dry run; the current
  code labels the size as an estimate and delegates removal to
  `flatpak uninstall --unused`.
- Old-kernel cleanup keeps the running kernel plus a recovery kernel and lets
  DNF perform removal.
- The cleanup audit log is append-only and deliberately has no automatic
  rotation or retention policy. It cannot silently delete history; manual
  archival/removal is documented.
- Permanent personal-file deletion is disabled because a same-UID quarantine
  cannot provide inode-identity protection. The historical private quarantine
  primitive is not user-reachable. Any future redesign requires a separate
  reviewed privilege boundary; do not re-enable it with another `stat`.
- The legacy quarantine code still has its historical recovery behavior in
  tests, but the product no longer creates `.appsweep-quarantine-*`
  directories. A future recovery workflow, if designed, must never
  automatically purge leftovers.
- A rename updates ctime, so the legacy post-quarantine identity check excludes
  ctime. ctime is checked on the user-visible entry before the move; device,
  inode, owner, size, and mtime are checked both before and after. This applies
  only to the private legacy primitive.
- Gio Trash remains pathname-based. The final-component `gio trash --` race is
  an accepted, recoverable same-UID limitation, not a claimed fix. A real
  disposable desktop test is still required.
- Python 3.14.9 was used for development; the declared minimum is Python 3.10
  and has not been tested on 3.10 in this environment.
- A real secondary filesystem or same-device bind mount was not created. Mount
  policy is covered by mountinfo unit fixtures for the scanner and simulated
  mount IDs for cache deletion and legacy quarantine regression tests; these
  are not real-mount integration evidence.
- The disposable-copy wheel build, inspection, isolated install, console-script
  check, and report-only smoke scan passed on Python 3.14.9. The `build`
  frontend and `twine` are unavailable, so no sdist or Twine check was run; the
  installed wheel was not tested on Fedora or Python 3.10.
- The GitHub Actions matrix has not been executed in this sandbox. It is a
  source-level check only; it does not replace the real-Fedora checklist.
- The final remediation, rename, and publication files are uncommitted in the
  working tree by instruction. `INDEPENDENT_VALIDATION_REPORT.md` remains
  untracked historical evidence and is excluded by `.gitignore`; no push, tag,
  history rewrite, package installation, or real cleanup was performed.

## Next milestone

See `TODO.md` and the manual checklist in `INTEGRATIONS.md`. The priority is a
real-Fedora validation pass for host tools and Gio Trash, an authorized real
bind-mount regression test, and a Python 3.10 run, followed by release review.
Missing tools in this sandbox are host limitations, not AppSweep implementation
failures. The follow-up changes must be reviewed and committed only after
explicit permission.
