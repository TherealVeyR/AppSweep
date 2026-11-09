# AppSweep Progress

Last updated: 2026-11-09
Status: **release candidate — code, cleanup audit logging, Git, packaging, docs,
and automated tests are in place; real-Fedora host-tool validation remains.**

## Current state

AppSweep is a working local Fedora storage analyzer and cleanup assistant.
It scans read-only, reports storage use and cleanup opportunities, lists the 50
largest personal files, supports numbered selection, requires an explicit
`CLEAN` confirmation, and defaults personal files to Trash.

`../taskvault` is unrelated and has not been touched.

## Completed work

### Core scanning and analysis

- [x] Shared data models for files, cleanup items, risk groups, actions,
  scans, reports, authorization, and execution results.
- [x] Extension-based file classification with conservative descriptions.
- [x] Mount discovery from `/proc/self/mountinfo`, local/network/virtual
  classification, mount-boundary checks, and filesystem capacity reporting.
- [x] Main scanner based on `os.scandir` with:
  - [x] boundary pruning
  - [x] symlink reporting without following
  - [x] hardlink accounting
  - [x] permission-error tracking
  - [x] skipped-path reporting
  - [x] cancellation with partial results
  - [x] bounded top-N personal-file heap
  - [x] personal-file filtering
  - [x] progress callback
- [x] Targeted directory measurement for cleanup discovery.
- [x] QUICK, FULL, and CUSTOM scan setup.
- [x] Quick scan covers common personal folders; full scan follows the live
  installation while skipping virtual, network, and external mounts; custom
  scan requires explicit `--path` roots.
- [x] FULL scan includes separately mounted local `/opt`, `/etc`, `/root`,
  `/srv`, and disk-backed `/tmp`, plus a read-only `/usr` image on immutable
  Fedora systems.
- [x] Flatpak sandbox detection and limitation reporting.

### Cleanup discovery and reporting

- [x] DNF/DNF5 package-cache, autoremove-preview, and old-kernel discovery.
- [x] rpm-ostree metadata-only cleanup discovery.
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

### Safety and execution

- [x] Safety policy with protected system paths, trees, home data, cache roots,
  and Trash roots.
- [x] Explicit cache cleanup cannot bypass `.ssh`, `.gnupg`, `.local`, or other
  protected home trees. The cache-root override is limited to
  `~/.var/app/<app>/cache`; Flatpak app data and configuration remain protected.
- [x] Snapshot revalidation by path, device, inode, size, mtime, owner, and
  group.
- [x] Symlink-safe cache clearing; symlinks are removed as entries, never
  traversed.
- [x] Personal-file permanent deletion only after separate typed confirmation
  and authorization bit.
- [x] Trash move through `gio trash --`; no automatic permanent-delete fallback.
- [x] Allowlisted command-shape validation for DNF, Flatpak, journalctl,
  rpm-ostree, and gio.
- [x] No `shell=True`; subprocess runner uses argument lists.
- [x] Per-command `pkexec` support for system actions; app refuses euid 0.
- [x] Cancellation disables cleanup during discovery.
- [x] Private append-only cleanup audit log in the user state directory:
  `0700` directory, `0600` JSON-lines file, symlink refusal, and
  started/finished/refused records.
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

- [x] Source launcher `bin/appssweep`.
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
- [x] Git repository initialized in the AppSweep project directory with a
  local commit identity and conventional, verified milestone commits.

## Verification evidence

### Automated tests

Command:

```bash
python3 -m compileall -q appssweep tests
python3 -m unittest discover -v
```

Result on 2026-11-09:

- **124 tests total**
- **123 passed, 1 skipped** without Rich
- **124 passed** in a venv with Rich installed
- **0 failures**

The suite covers selection parsing, filesystem measurement, scanner traversal,
symlinks, permissions, mount boundaries, hardlink accounting, cancellation,
full-mode root discovery, safety/authorization, cache override protection,
package and Flatpak detection, runner safety, cleanup engine behavior,
reporting/export, CLI flows, refresh-metadata wiring, CSV formula injection,
analyzer overlap handling, exact top-50 display/selection numbering,
cleanup audit-log privacy and refusal paths, Trash content previews,
narrow-terminal rendering, Rich failure fallback, and the `--dry-run` alias.

### Manual smoke tests

- [x] `./bin/appssweep --mode quick ... --report-only --json ... --csv ...`
  against an isolated temporary home: exit 0, correct ranking, valid JSON/CSV.
- [x] `./bin/appssweep --mode custom --path ...`: exit 0, correct custom root
  and file.
- [x] `./bin/appssweep --mode full` in the Flatpak sandbox: exit 0, correct
  sandbox limitation, isolated home scanned.
- [x] `python3 -m unittest discover` in a venv with Rich: all 124 tests pass.
- [x] `ruff check appssweep tests` in the Rich venv: all checks pass
  (optional lint; Ruff is not a runtime dependency).
- [x] `pip install .` in a clean venv: console script reports `appssweep 1.0.0`
  and completes a quick scan.
- [x] `pip wheel --no-deps .` builds `appssweep-1.0.0-py3-none-any.whl`;
  inspection confirms `appssweep/audit.py` and the `appssweep =
  appssweep.cli:main` console entry point are present.
- [x] Rich manual rendering check: `supports_rich() == True`, banner and storage
  content render without error.
- [x] Audit-log tests verify the `0600` file, `0700` directory, JSON-lines
  records, symlink refusal, hard-link refusal, and unsafe-directory refusal.
- [x] Interactive CLI test verifies started/finished audit records after a
  confirmed temporary cache cleanup.
- [x] CLI test verifies an unsafe audit-log directory produces a warning while
  the confirmed temporary cache cleanup still completes.
- [x] Cleanup engine test rejects duplicate personal-file paths in a plan.
- [x] Trash tests verify size, top-level count, example contents, and the
  no-`gio` report-only path.
- [x] CLI test verifies `--refresh-metadata` reaches discovery and the default
  remains false.
- [x] Export tests cover a missing parent, spaces in the report path, and a
  read-only parent directory with a clear `ExportError`.
- [x] UI tests cover 64-column rendering with a long unbroken name and fallback
  to the plain stream when the Rich console raises.

## Known limitations and unverified areas

- The sandbox lacks real `dnf`, `rpm-ostree`, `flatpak`, `journalctl`, `rpm`,
  and `pkexec`; `gio` 2.88.3 is present but was not used against the real host
  Trash during development. Host integrations are tested with mocks and
  command-shape validation only.
- Full host filesystem scanning and real package-manager cleanup have not been
  run on a full Fedora host in this environment.
- Flatpak 1.18 has no supported non-interactive unused-ref dry run; the current
  code labels the size as an estimate and delegates removal to
  `flatpak uninstall --unused`.
- Old-kernel cleanup keeps the running kernel plus a recovery kernel and lets
  DNF perform removal.
- The cleanup audit log is append-only and currently has no automatic rotation
  or retention policy.
- Python 3.14 was used for development; the declared minimum is Python 3.10 and
  has not been tested on 3.10 in this environment.

## Next milestone

See `TODO.md`. The priority is real-Fedora validation of host tools, followed
by any regression tests or fixes found there.
