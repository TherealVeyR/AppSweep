# AppSweep Architecture

Last updated: 2026-11-11

## Purpose

AppSweep is a safety-first storage analyzer and cleanup assistant for Fedora.
It separates four concerns:

1. **Observe** the filesystem without changing it.
2. **Explain** storage use and cleanup opportunities in plain language.
3. **Select** exact numbered items with an unambiguous mapping.
4. **Execute** only an explicitly confirmed, allowlisted plan.

The design assumes file names, paths, command output, and package metadata are
untrusted. Safety checks are layered so a bug in one layer does not authorize a
destructive operation elsewhere.

The threat model covers accidental selection, path traversal, symlink and
parent-directory replacement by actors outside the current user's trust
boundary, mount-boundary escapes, and stale snapshots. It explicitly excludes a
hostile or concurrent process running with the same effective UID: such a
process can already modify or delete every file the user can access, and an
unprivileged application cannot create a security boundary against it.
Permanent personal-file deletion is therefore disabled rather than claiming
that a mode-0700 quarantine is such a boundary. Moving personal files to Trash
is retained as a recoverable operation with a documented same-UID pathname
race.

## High-level flow

```text
CLI arguments
    │
    ├─ refuse euid 0
    ├─ resolve QUICK / FULL / CUSTOM roots
    ├─ parse categories, sizes, exports
    ▼
Read-only scan (StorageScanner)
    ├─ mount discovery; prune every nested mount boundary
    ├─ descriptor-relative directory traversal, no symlink following
    ├─ global hardlink accounting
    ├─ permission and skipped-path reporting
    ├─ bounded top-N personal-file candidate map
    └─ progress + incremental cancellation
    ▼
Discovery (discover_cleanup)
    ├─ detect optional host tools from PATH (no execution)
    ├─ packages / rpm-ostree
    ├─ Flatpak
    ├─ caches
    ├─ logs / journal / temp
    └─ Trash
    ▼
Report assembly (build_report)
    ├─ dedupe and number cleanup items
    ├─ group by SAFE / CAREFUL / REBUILD
    ├─ detect overlaps and disable unsafe duplicates
    ├─ remove personal/category overlaps
    ├─ rank the top 50 personal files
    └─ add notes and warnings
    ▼
Presentation (UI)
    ├─ storage summary
    ├─ host integration availability and limits
    ├─ numbered cleanup categories
    ├─ separate top-50 personal table
    └─ report-only findings
    ▼
Optional export
    ├─ JSON (complete payload)
    └─ CSV (record_type rows, formula-safe)
    ▼
Interactive selection
    ├─ personal file numbers
    ├─ Trash-only personal choice; permanent deletion disabled
    ├─ cleanup item numbers / groups / exclusions
    └─ combined plan + typed CLEAN
    ▼
Authorization + execution (CleanupEngine)
    ├─ verify authorization covers the exact plan
    ├─ reject ineligible/duplicate/overlapping actions
    ├─ revalidate every personal snapshot
    ├─ append cleanup_started to the private audit log
    ├─ run allowlisted actions one at a time
    ├─ cache/personal deletion through verified descriptors
    ├─ append cleanup_finished or cleanup_refused
    └─ report before/after free space
```

## Module map

| Module | Responsibility |
| --- | --- |
| `appsweep/__init__.py` | App name, tagline, version. |
| `appsweep/__main__.py` | `python -m appsweep` entry point. |
| `appsweep/cli.py` | Argument parsing, scan setup, mode selection, root refusal, workflow orchestration, exports, interactive cleanup. |
| `appsweep/audit.py` | Private append-only cleanup audit log: path resolution, safe directory/file creation, JSON-lines plan/result records. |
| `appsweep/models.py` | Shared dataclasses/enums: `RiskGroup`, `ActionKind`, `FileSnapshot`, `ScanConfig`, `ScanResult`, `CleanupItem`, `CleanupPlan`, `Authorization`, execution results, report models. |
| `appsweep/filesystem.py` | Mount parsing, local/network/virtual classification, boundary checks, targeted measurement, filesystem capacity discovery. |
| `appsweep/scanner.py` | Descriptor-relative read-only traversal, global hardlink accounting, mount pruning, progress, cancellation callbacks, permission tracking, bounded top-N candidate map, personal-file filtering. |
| `appsweep/filetypes.py` | Conservative extension-based file classification. |
| `appsweep/large_files.py` | Quick-scan folder list, personal exclusion rules, application roots, ranking helpers, selection map. |
| `appsweep/capabilities.py` | Read-only PATH detection for optional host tools, actionable limits, and explicit mock/unverified status. |
| `appsweep/categories.py` | Runs cleanup discovery modules, records capability status, dedupes, sorts, and numbers items. |
| `appsweep/analyzer.py` | Report assembly, group totals, overlap detection, warnings, notes, report-only merge. |
| `appsweep/package_manager.py` | DNF/DNF5 and rpm-ostree detection, cache discovery, autoremove preview parsing, old-kernel selection and command previews. |
| `appsweep/flatpak.py` | Flatpak command discovery, runtime listings, unused-runtime estimate, official cleanup command. |
| `appsweep/cache_cleaner.py` | User/system/app cache discovery and scoped cleanup items. |
| `appsweep/log_cleaner.py` | Journal, rotated logs, crash dumps, temporary files, and report-only system data. |
| `appsweep/trash_manager.py` | Trash size/entry discovery and `gio trash --empty` item. |
| `appsweep/safety.py` | Protected path policy, snapshot revalidation, descriptor-relative cache clearing, legacy private-quarantine code retained out of reach, restoration primitives, mount-ID boundary checks, authorization coverage, fail-closed permanent-deletion refusal. |
| `appsweep/cleanup_engine.py` | Exact plan validation, allowlisted command validation, execution, Trash-only personal handling, fail-closed permanent-plan refusal, free-space measurement. |
| `appsweep/runner.py` | Subprocess wrapper using argument lists; `pkexec` only when requested; sanitizes and bounds output. |
| `appsweep/selection.py` | Number/range/group/all/none/cancel/exclusion parser and exact resolution. |
| `appsweep/reporting.py` | JSON/CSV rendering, formula-safe cells, atomic private writes. |
| `appsweep/ui.py` | ANSI UI, optional Rich rendering, terminal escaping, prompts and confirmation summaries. |
| `appsweep/util.py` | Size/time formatting, safe terminal text, path containment, command display, stopwatch. |
| `bin/appsweep` | Source-checkout launcher that adds the project root to `sys.path`. |

## Data model essentials

`FileSnapshot` identifies a personal file with:

- normalized path
- logical size and allocated size
- mtime, ctime (nanoseconds), device, inode
- extension, type label, kind, description
- owner UID
- risk group (always `CAREFUL` for personal files)

The identity fields are re-checked before any move. Change time is checked on
the user-visible entry during snapshot validation; it is not used after a
Gio handoff because Gio owns the move. The ctime check rejects same-size
in-place edits whose mtime was restored.

`CleanupItem` contains:

- stable unique `key`
- display number assigned after discovery
- title, group, size, summary
- plain-language description, consequences, method
- allowlisted `ActionKind`
- optional paths and command argument tuples
- estimated flag, eligibility flag, report-only reason
- administrator requirement
- details and preview text

`ScanConfig` is normalized before use. Normalization:

- removes empty/invalid paths
- keeps only existing paths
- deduplicates roots
- clamps sizes and top-N values
- forces symlink following off
- normalizes exclusions and application roots

`Authorization` is a capability record:

- set of cleanup item keys
- set of personal file paths
- separate `permanent_personal` boolean retained for compatibility; the engine
  refuses it before any action

`authorization_covers()` rejects any plan that contains an item or path not
explicitly authorized.

## Scanning design

### Roots and modes

- **QUICK** uses existing common personal folders under home. If none exist, it
  falls back to the home folder itself.
- **FULL** starts from `/`, adds separately mounted local `/home`, `/var`,
  `/usr`, `/boot`, `/opt`, `/etc`, `/root`, `/srv`, and disk-backed `/tmp`
  when appropriate, and removes duplicate nested roots on the same device. A
  read-only `/usr` image on an immutable Fedora system is included for storage
  analysis. Every mount boundary nested below a traversal root is pruned,
  including same-device bind mounts, so one tree is not counted through two
  paths. A separately mounted local directory is scanned from its own explicit
  root. Virtual, network, and external mounts are skipped.
- **CUSTOM** requires one or more existing `--path` values. A custom root inside
  a normally skipped mount is allowed because the user explicitly selected it.
  An explicitly selected nested mount root is scanned once; other nested mount
  points below it remain pruned.

A Flatpak sandbox cannot reach the host root reliably, so FULL mode falls back
to the accessible home folder and reports the limitation. Memory-backed tmpfs is
not counted as disk storage in a full scan, but `/tmp` and `/var/tmp` are still
examined by the report-only temporary-file discovery step.

### Traversal

`StorageScanner` uses `os.scandir` through opened directory descriptors with an
explicit stack rather than `os.walk`:

- path entries are inspected with `lstat` / `follow_symlinks=False`; child
  stats use `dir_fd`
- queued directories are reopened with `O_DIRECTORY | O_NOFOLLOW`, then matched
  by device and inode before use
- directory entries are enumerated incrementally and cancellation is consulted
  inside the loop, so a huge directory does not need to be fully materialized
- symlinks are counted and reported, never followed
- excluded and protected paths are pruned
- every nested mount point is detected from the live mount table and pruned;
  an explicitly selected nested root is still scanned from its own entry
- hardlinks are accounted once globally for storage, across all roots and
  loose-file roots, via `(st_dev, st_ino)`. Personal eligibility is still
  evaluated for every pathname, including an alias whose storage was counted
  under an earlier non-personal root. Multiple eligible aliases of one inode use
  the largest snapshot, then the lexicographically smaller path, as the
  deterministic Top 50 representative.
- directories accumulate logical and allocated sizes for top-directory output
- permission failures are captured, counted, and sampled
- cancellation is checked periodically and through convenience callbacks; it
  preserves partial results
- a bounded per-inode candidate map keeps only the largest N personal
  representatives and updates an alias without evicting another inode
- progress is throttled

Personal candidates must be regular files owned by the current user, above the
minimum size, inside personal roots, and outside caches, hidden configuration,
Trash, and system data. The final report sorts by `(-size, path)`, so display
and selection use the same order.

## Cleanup action model

`ActionKind` is the complete allowlist:

- `CLEAR_CACHE_CONTENTS`
- `DNF_CLEAN_CACHE`
- `DNF_AUTOREMOVE`
- `DNF_OLD_KERNELS`
- `FLATPAK_UNUSED`
- `JOURNAL_VACUUM`
- `EMPTY_TRASH`
- `RPM_OSTREE_METADATA`
- `TRASH_PERSONAL`
- `PERMANENT_PERSONAL` (retained in the enum but disabled and not selectable)
- `REPORT_ONLY`

Category items may not use the personal-file actions. Validation happens twice:

1. Discovery produces a transparent command preview and obvious eligibility.
2. `validate_item_command()` re-checks the exact executable and argument shape
   immediately before execution.

There is no code path that accepts an arbitrary command or arbitrary path from
stdin or the command line.

## Safety model

### Protected paths

`SafetyPolicy` rejects direct filesystem cleanup at or beneath:

- filesystem roots and system trees (`/`, `/etc`, `/usr`, `/var`, `/boot`,
  `/proc`, `/sys`, `/dev`, `/run`, and related locations)
- the whole home folder or a scan root itself
- sensitive home trees (`.ssh`, `.gnupg`, `.pki`, `.config`, `.local`,
  `.mozilla`, `.thunderbird`, `.password-store`, `.var`, `.steam`, `.wine`,
  `.android`, `.docker`)
- top-level cache folders

Cache cleanup uses explicit discovered child cache roots and keeps the target
folder itself. The target is opened through a verified directory descriptor;
directory entries are enumerated and removed relative to descriptors, and child
directory descriptors are checked for the expected device and mount ID. A
different filesystem or mount boundary, including a same-device bind mount, is
never crossed. If the required no-follow/descriptor primitives or
`/proc/self/fdinfo` mount-ID verification are unavailable, cache cleanup fails
closed rather than falling back to a path-only removal. A cache root may
override the generic home-tree guard only for `~/.var/app/<app>/cache`;
sensitive trees such as `.ssh`, `.gnupg`, `.local`, and Flatpak app data or
configuration cannot be reached through a misconfigured cache root.

### Personal files

- Default and only exposed action: `gio trash -- <path>`.
- If `gio` is unavailable, the operation fails; there is no permanent-delete
  fallback.
- Permanent personal-file deletion is disabled. The CLI offers no
  `DELETE PERMANENTLY` prompt, `CleanupEngine` rejects a plan with
  `permanent_personal=True` before authorization, validation, or filesystem
  work, and the public `SafetyPolicy.remove_personal_file` method raises
  `SafetyError` without moving or unlinking anything.
- The reason is a genuine boundary failure, not a missing check: a process
  running as the same UID can replace the quarantine entry between validation
  and the final `unlink`. A normal mode-0700 directory owned by that UID is not
  a separate security boundary, and another `stat` immediately before `unlink`
  is not a race fix. The only safe outcomes available without a newly reviewed
  privileged helper are refusal or a quarantine that never unlinks; AppSweep
  chooses refusal.
- The historical private-quarantine algorithm remains in `safety.py` as
  `_remove_personal_file_quarantine_legacy` only for regression/history and
  future redesign. It is private, is not called by application code, and must
  not be exposed.
- Before execution, the snapshot must match path, owner, group, device, inode,
  size, mtime, and ctime. The ctime check rejects a same-size in-place edit
  whose mtime was restored.
- Moving to Trash revalidates the snapshot immediately before invoking Gio, but
  Gio resolves a pathname. A same-UID final-component swap can still redirect
  the handoff to a different file. This is an accepted, recoverable limitation:
  the wrong file can be moved to Trash but is not permanently deleted. It is
  **not** claimed fixed by revalidation. Real Gio remains blocked for
  host-validation purposes.
- Parent symlinks, changed ownership, non-regular files, cache paths, Trash
  paths, and hidden paths are rejected.

### System commands

- Actions run one at a time.
- `pkexec` wraps only the specific command marked `needs_admin`.
- `sudo` is not used.
- Output is bounded and sanitized before display.
- A failed command is reported and later commands in that item are not run.

### DNF preview metadata refresh

The DNF autoremove preview runs with `--assumeno --cacheonly` by default. If
`--refresh-metadata` is supplied and package discovery is enabled, AppSweep may
retry the preview without `--cacheonly`, which can update local repository
metadata during a report-only or dry-run invocation. This is an explicit side
effect: AppSweep prints a warning before discovery, ignores the flag with an
explanatory warning when package discovery is disabled, and changes the final
report-only text to “No cleanup actions were performed; DNF repository metadata
may have been refreshed.” A dry-run without the flag remains cache-only.

### Cleanup audit logging

After the user types `CLEAN`, `appsweep/audit.py` appends a JSON-lines record
before execution and another record after success or refusal. The default path
is:

```text
$XDG_STATE_HOME/appsweep/cleanup-audit.log
```

If `XDG_STATE_HOME` is unset, it falls back to:

```text
~/.local/state/appsweep/cleanup-audit.log
```

The `appsweep` directory is opened by descriptor with
`O_DIRECTORY | O_NOFOLLOW` and enforced as `0700`; an existing `0755` or `0711`
directory is tightened before use. Group/world-writable and foreign-owned
directories are refused. The log is opened relative to that descriptor with
`O_APPEND | O_CREAT | O_NOFOLLOW` and forced to `0600`. The logger also refuses
a symlink, non-regular file, or hard-linked log. Records include the app
version, UID, exact plan, authorization summary, execution results, and refusal
error. Filesystem text is JSON-escaped, not interpreted as terminal output.

An audit-write failure is shown as a warning and does not roll back or hide an
already completed cleanup. The log is append-only and currently has no built-in
rotation.

## Presentation and language

The three groups are fixed and user-facing:

- **SAFE** — temporary by-products such as caches and logs.
- **CAREFUL** — personal files or Trash.
- **REBUILD** — package data that can be downloaded or installed again.

Every cleanup item explains what it is, where it is, why it appears, what
happens if cleaned, and how AppSweep cleans it. Estimated sizes are labeled as
estimates. Report-only findings are shown separately and cannot be selected.
Trash previews include the size, top-level count, and up to eight example item
names before the user can select the emptying action.

The UI uses ANSI by default. If Rich is importable and colour is enabled, Rich
renders the same text. If Rich raises during output, the UI disables Rich and
falls back to ANSI.

## Export design

JSON preserves the full report:

- app/version/timestamps
- scan configuration and coverage
- filesystem capacity
- cleanup items and action labels
- personal files with exact byte sizes
- notes, warnings, overlaps
- categories run, host capability status, package manager, Flatpak, and
  report-only findings

CSV uses a single table with a `record_type` column:

- `personal_file`
- `cleanup_item`
- `report_only`

Text cells that could be interpreted as spreadsheet formulas are prefixed with
an apostrophe. Exports are written to a temporary file in the destination
directory, flushed, `chmod 0600`, and atomically replaced.

## CLI modes and exit behavior

- Report-only is the default when stdin is not a terminal.
- `--dry-run` is an alias for `--report-only`; both are read-only and never
  offer cleanup.
- `--interactive` enables prompts in non-TTY contexts.
- Cancelled scans keep a partial report, disable cleanup, and exit 130.
- Usage errors and root refusal exit 2.
- Export or cleanup failures exit 1.
- `--no-color`, `NO_COLOR`, and non-TTY streams disable colour.

## Technical decisions

### Descriptor-relative filesystem operations

Paths are treated as untrusted between the scan and the destructive operation.
Cache clearing anchors an opened, verified target descriptor, opens child
directories with `O_DIRECTORY | O_NOFOLLOW`, verifies `fstat` identity, performs
stat/unlink/rmdir with `dir_fd`, and rechecks the path-to-descriptor identity
before the destructive loop. Personal files are revalidated and handed to Gio;
permanent deletion fails closed before any filesystem operation. Mount IDs come
from `/proc/self/fdinfo/<fd>` so a renamed path or bind mount cannot redirect
cache operations. These operations fail closed when the platform lacks the
required primitives.

### Bounded selection parsing

Selection ranges are checked against the available numbered choices before any
set is constructed. A reversed range, an endpoint beyond the last choice, or a
span larger than the choice list is rejected. Integer parsing is bounded so an
absurdly long number produces a normal selection error instead of an uncaught
`ValueError` or unbounded allocation.

### Standard library first

There are no required Python dependencies. This keeps installation simple on
Fedora and avoids supply-chain surface for a tool that inspects and removes
files. Rich is optional and isolated behind a guarded import.

### No shell

Commands are tuples passed directly to `subprocess.run`. `format_command()` is
display-only and never executed. This prevents shell metacharacter injection
through package names or paths.

### Official tools for system cleanup

DNF, rpm-ostree, Flatpak, journalctl, and gio own their data formats and
transactions. AppSweep does not reimplement package metadata deletion,
deployments, runtime removal, journal vacuuming, or Trash semantics.

### Graceful capability detection

`appsweep/capabilities.py` checks PATH visibility for optional host commands
without executing them. Missing tools are reported as limits, not application
errors: only the dependent cleanup categories are disabled. The capability
report is shown before cleanup selection, included in JSON, and records
`real_host_verified: false` in this release. Only recorded evidence from the
manual checklist in `INTEGRATIONS.md` can justify changing that status.
Automated coverage uses mocked tools and is labelled as such.

### Conservative Flatpak estimate

Flatpak 1.18 has no supported non-interactive unused-ref dry run. The discovery
code labels the candidate size as an estimate and delegates removal to
`flatpak uninstall --unused`. It does not claim exact reclaim.

### Old kernels

Old-kernel cleanup keeps the running kernel plus a recovery kernel and delegates
removal to DNF. It does not modify bootloader deployments or rollback state.

### Scan and report separation

The scanner produces one snapshot. Report assembly, display, selection, JSON,
and CSV all derive from that same ordered list, so numbering cannot drift.

### Atomic reports

Exports use a same-directory temporary file, `fsync`, `chmod 0600`, and
`os.replace`. A failed export leaves the previous report intact and reports an
error instead of a traceback.

### Private cleanup audit history

The audit log is deliberately separate from exports: exports describe a scan,
while the audit log describes an authorized cleanup attempt. Keeping it local
and private provides accountability without a database, daemon, or network
service. Audit failure is a warning rather than a cleanup rollback because the
filesystem operation may already have completed; hiding that result would be
less truthful than reporting both the result and the logging failure.

## Testing architecture

Tests use only `unittest`, temporary directories, and fakes:

- `tests/helpers.py` creates real temporary files, snapshots, unreadable
  directories, and a `FakeRunner`.
- `FakeRunner` implements the subset of `CommandRunner` used by discovery and
  cleanup, records calls, and returns scripted results.
- Host tools are mocked rather than invoked. Real Fedora host validation is
  still required.
- `tests/test_capabilities.py` verifies PATH-only capability detection and
  warnings; `tests/test_host_integration_mocks.py` is explicitly labelled as
  mocked coverage for rpm-ostree, RPM, and journalctl paths.
- Scanner tests create real directory trees in temporary roots, including
  directory-replacement races, cross-root hard links in both root orders,
  deterministic personal-alias representatives, a same-size alias update that
  must not evict another personal file, same-device mountinfo fixtures, and
  cancellation during a wide directory.
- Safety tests exercise real `lstat`/unlink/cache-removal behavior inside
  temporary directories. Deterministic race tests replace cache targets, nested
  cache directories, personal parents, and selected files at controlled
  check/use windows. Legacy quarantine regressions cover visible-path,
  parent-directory, symlink, directory, and final-unlink replacement; they
  exercise the private legacy primitive and are labelled as such. Public
  permanent deletion is separately tested to refuse before any rename or
  unlink, including the audit's same-UID substitution scenario. Same-size
  content edits with restored mtimes are rejected. Real bind mounts are not
  created; mount-boundary policy branches use simulated mount IDs and are
  labelled as such.
- CLI tests run `main()` with redirected streams and temporary HOME/XDG
  variables.
- Audit tests use temporary state directories and verify privacy, JSON-lines
  records, symlink and hard-link refusal, unsafe-directory refusal, and
  tightening of existing `0755`/`0711` directories to `0700`.
- Trash tests verify size, count, example contents, and the no-`gio`,
  report-only path.
- UI tests verify exact top-50 numbering and optional Rich rendering.

The suite never performs destructive cleanup outside temporary test roots. Race
tests that depend on the current user's permissions are skipped under root.

## Non-goals

- Cross-platform support beyond Linux/Fedora.
- Running as a privileged daemon.
- Scanning remote/network filesystems automatically.
- Automating bootloader, rollback, or deployment changes.
- Providing a general-purpose file manager or recursive delete tool.
- Guaranteeing that logical file sizes equal physical disk use on
  compression/reflink filesystems.
