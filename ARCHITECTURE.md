# AppSweep Architecture

Last updated: 2026-11-09

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

## High-level flow

```text
CLI arguments
    │
    ├─ refuse euid 0
    ├─ resolve QUICK / FULL / CUSTOM roots
    ├─ parse categories, sizes, exports
    ▼
Read-only scan (StorageScanner)
    ├─ mount discovery and boundary pruning
    ├─ os.scandir traversal, no symlink following
    ├─ hardlink accounting
    ├─ permission and skipped-path reporting
    ├─ top-N personal-file heap
    └─ progress + cancellation
    ▼
Discovery (discover_cleanup)
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
    ├─ permanent-vs-Trash choice
    ├─ cleanup item numbers / groups / exclusions
    └─ combined plan + typed CLEAN
    ▼
Authorization + execution (CleanupEngine)
    ├─ verify authorization covers the exact plan
    ├─ reject ineligible/duplicate/overlapping actions
    ├─ revalidate every personal snapshot
    ├─ append cleanup_started to the private audit log
    ├─ run allowlisted actions one at a time
    ├─ append cleanup_finished or cleanup_refused
    └─ report before/after free space
```

## Module map

| Module | Responsibility |
| --- | --- |
| `appssweep/__init__.py` | App name, tagline, version. |
| `appssweep/__main__.py` | `python -m appssweep` entry point. |
| `appssweep/cli.py` | Argument parsing, scan setup, mode selection, root refusal, workflow orchestration, exports, interactive cleanup. |
| `appssweep/audit.py` | Private append-only cleanup audit log: path resolution, safe directory/file creation, JSON-lines plan/result records. |
| `appssweep/models.py` | Shared dataclasses/enums: `RiskGroup`, `ActionKind`, `FileSnapshot`, `ScanConfig`, `ScanResult`, `CleanupItem`, `CleanupPlan`, `Authorization`, execution results, report models. |
| `appssweep/filesystem.py` | Mount parsing, local/network/virtual classification, boundary checks, targeted measurement, filesystem capacity discovery. |
| `appssweep/scanner.py` | Main read-only traversal, progress, cancellation, hardlink accounting, permission tracking, top-N heap, personal-file filtering. |
| `appssweep/filetypes.py` | Conservative extension-based file classification. |
| `appssweep/large_files.py` | Quick-scan folder list, personal exclusion rules, application roots, ranking helpers, selection map. |
| `appssweep/categories.py` | Runs cleanup discovery modules, dedupes, sorts, and numbers items. |
| `appssweep/analyzer.py` | Report assembly, group totals, overlap detection, warnings, notes, report-only merge. |
| `appssweep/package_manager.py` | DNF/DNF5 and rpm-ostree detection, cache discovery, autoremove preview parsing, old-kernel selection and command previews. |
| `appssweep/flatpak.py` | Flatpak command discovery, runtime listings, unused-runtime estimate, official cleanup command. |
| `appssweep/cache_cleaner.py` | User/system/app cache discovery and scoped cleanup items. |
| `appssweep/log_cleaner.py` | Journal, rotated logs, crash dumps, temporary files, and report-only system data. |
| `appssweep/trash_manager.py` | Trash size/entry discovery and `gio trash --empty` item. |
| `appssweep/safety.py` | Protected path policy, snapshot revalidation, symlink-safe cache removal, personal unlink, authorization coverage. |
| `appssweep/cleanup_engine.py` | Exact plan validation, allowlisted command validation, execution, Trash/permanent handling, free-space measurement. |
| `appssweep/runner.py` | Subprocess wrapper using argument lists; `pkexec` only when requested; sanitizes and bounds output. |
| `appssweep/selection.py` | Number/range/group/all/none/cancel/exclusion parser and exact resolution. |
| `appssweep/reporting.py` | JSON/CSV rendering, formula-safe cells, atomic private writes. |
| `appssweep/ui.py` | ANSI UI, optional Rich rendering, terminal escaping, prompts and confirmation summaries. |
| `appssweep/util.py` | Size/time formatting, safe terminal text, path containment, command display, stopwatch. |
| `bin/appssweep` | Source-checkout launcher that adds the project root to `sys.path`. |

## Data model essentials

`FileSnapshot` identifies a personal file with:

- normalized path
- logical size and allocated size
- mtime, device, inode
- extension, type label, kind, description
- owner UID
- risk group (always `CAREFUL` for personal files)

The identity fields are re-checked before any move or unlink.

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
- separate `permanent_personal` boolean

`authorization_covers()` rejects any plan that contains an item or path not
explicitly authorized.

## Scanning design

### Roots and modes

- **QUICK** uses existing common personal folders under home. If none exist, it
  falls back to the home folder itself.
- **FULL** starts from `/`, adds separately mounted local `/home`, `/var`,
  `/usr`, and `/boot` when appropriate, and removes duplicate nested roots on
  the same device. It skips virtual, network, and external mounts.
- **CUSTOM** requires one or more existing `--path` values. A custom root inside
  a normally skipped mount is allowed because the user explicitly selected it.

A Flatpak sandbox cannot reach the host root reliably, so FULL mode falls back
to the accessible home folder and reports the limitation. Memory-backed tmpfs is
not counted as disk storage in a full scan, but `/tmp` and `/var/tmp` are still
examined by the report-only temporary-file discovery step.

### Traversal

`StorageScanner` uses `os.scandir` with an explicit stack rather than
`os.walk`:

- `lstat` / `follow_symlinks=False` everywhere
- symlinks are counted and reported, never followed
- excluded and protected paths are pruned
- mount boundaries are detected from the live mount table
- hardlinks are counted once for storage and ranking via `(st_dev, st_ino)`
- directories accumulate logical and allocated sizes for top-directory output
- permission failures are captured, counted, and sampled
- cancellation is checked periodically and preserves partial results
- a bounded heap keeps only the largest N personal candidates
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
- `PERMANENT_PERSONAL`
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
folder itself. Filesystem boundaries inside caches are not crossed. A cache
root may override the generic home-tree guard only for
`~/.var/app/<app>/cache`; sensitive trees such as `.ssh`, `.gnupg`, `.local`,
and Flatpak app data or configuration cannot be reached through a
misconfigured cache root.

### Personal files

- Default action: `gio trash -- <path>`.
- If `gio` is unavailable, the operation fails; there is no permanent-delete
  fallback.
- Permanent unlink requires the separate `DELETE PERMANENTLY` phrase and the
  `permanent_personal` authorization bit.
- Before execution, the snapshot must match path, owner, group, device, inode,
  size, and mtime.
- Parent symlinks, changed ownership, non-regular files, cache paths, Trash
  paths, and hidden paths are rejected.

### System commands

- Actions run one at a time.
- `pkexec` wraps only the specific command marked `needs_admin`.
- `sudo` is not used.
- Output is bounded and sanitized before display.
- A failed command is reported and later commands in that item are not run.

### Cleanup audit logging

After the user types `CLEAN`, `appssweep/audit.py` appends a JSON-lines record
before execution and another record after success or refusal. The default path
is:

```text
$XDG_STATE_HOME/appssweep/cleanup-audit.log
```

If `XDG_STATE_HOME` is unset, it falls back to:

```text
~/.local/state/appssweep/cleanup-audit.log
```

The `appssweep` directory is created with mode `0700`; the log is opened with
`O_APPEND | O_CREAT | O_NOFOLLOW` and forced to `0600`. The logger refuses a
symlink, non-regular file, foreign-owned directory, or group/world-writable
directory. Records include the app version, UID, exact plan, authorization
summary, execution results, and refusal error. Filesystem text is JSON-escaped,
not interpreted as terminal output.

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
- categories run, package manager, Flatpak, and report-only findings

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
- Host tools are mocked rather than invoked.
- Scanner tests create real directory trees in temporary roots.
- Safety tests exercise real `lstat`/unlink/cache-removal behavior inside
  temporary directories.
- CLI tests run `main()` with redirected streams and temporary HOME/XDG
  variables.
- Audit tests use temporary state directories and verify privacy, JSON-lines
  records, symlink refusal, and unsafe-directory refusal.
- UI tests verify exact top-50 numbering and optional Rich rendering.

The suite never performs destructive cleanup outside temporary test roots.

## Non-goals

- Cross-platform support beyond Linux/Fedora.
- Running as a privileged daemon.
- Scanning remote/network filesystems automatically.
- Automating bootloader, rollback, or deployment changes.
- Providing a general-purpose file manager or recursive delete tool.
- Guaranteeing that logical file sizes equal physical disk use on
  compression/reflink filesystems.
