# Changelog

All notable changes to AppSweep are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
AppSweep uses semantic versioning once public releases begin. This project has
not been published to a package registry.

## [Unreleased]

### Added

- Private, append-only cleanup audit logging under the user state directory.
  Confirmed cleanup plans are recorded before execution and again with the
  result or refusal.
- `--dry-run` as an explicit alias for `--report-only`. It never offers or
  executes cleanup.
- Read-only PATH capability detection for optional Fedora host tools. The
  terminal now shows a `HOST INTEGRATIONS` section with available tools,
  missing tools, and actionable limits; JSON reports include the same status.
- `INTEGRATIONS.md` with the implementation matrix, mocked-coverage inventory,
  current sandbox status, and a manual real-Fedora validation checklist.
- `SECURITY.md` with the threat model, same-UID boundary statement, Gio
  limitation, and private vulnerability-reporting guidance.
- `PUBLISHING.md` with the detailed pre-publication, Git, GitHub, wheel, and
  release guide.
- MIT `LICENSE` and package license metadata.
- GitHub Actions compile/unittest matrix for Python 3.10 through 3.14.
- Disposable-copy wheel build, archive inspection, isolated install, uninstall,
  console script, and report-only smoke verification for the final source.
- Mocked integration tests for rpm-ostree metadata, RPM old-kernel selection,
  journal usage parsing, journalctl vacuum discovery, and the
  no-journalctl report-only fallback.
- FULL scan root discovery now includes separately mounted local `/opt`, `/etc`,
  `/root`, `/srv`, and disk-backed `/tmp`, plus a read-only `/usr` image on
  immutable Fedora systems.
- Trash previews now show the top-level item count and up to eight example
  contents before emptying.
- Persistent release documentation: `CHANGELOG.md`, `CONTRIBUTING.md`, and
  `RELEASE_CHECKLIST.md`.
- Git repository hygiene rules for generated reports, test state, secrets,
  local configuration, and build artifacts.

### Fixed

- Journal usage parsing now accepts the short units printed by real
  `journalctl` (for example `1.0G`) as well as IEC units such as `1.0GiB`.
- Resolved the earlier independent validation findings `BUG-001` through
  `BUG-004` and the report's `POT-001` through `POT-005` in the working tree.
- Follow-up `BUG-005`: a personal hard-link alias is no longer omitted when the
  inode's storage was counted under an earlier non-personal scan root. Storage
  is still counted once globally; multiple eligible aliases use one
  deterministic Top 50 path. Superseding an alias no longer consumes a Top 50
  slot or evicts an unrelated same-size personal file.
- Follow-up `BUG-006` is closed by **disabling permanent personal-file
  deletion**. The previous quarantine flow could be manipulated by another
  process running as the same UID after validation and before `unlink`, which
  could delete the wrong inode and misreport reclaimed bytes. The public safety
  API now refuses before any rename or unlink; the engine rejects a permanent
  plan before any filesystem work; the CLI offers no permanent-deletion prompt.
  The historical quarantine algorithm is retained only as a private legacy
  primitive for regression/history and future redesign.
- Follow-up `BUG-007`: an existing `0755` or `0711` audit directory is now
  tightened to `0700`; group/world-writable directories remain refused.
- Same-size in-place edits with a restored modification time are rejected by a
  new `ctime_ns` snapshot check before any move. The private legacy
  post-quarantine check excludes ctime because the rename itself updates it;
  the public permanent-deletion path is disabled.
- Cross-root and loose-file hard links are now deduplicated globally across the
  entire scan, not only within one root.
- Convenience `cancel_check` callbacks passed to the scanner are now consulted;
  they can stop immediately or during traversal. Wide directories are
  enumerated incrementally rather than materialized before the first check.
- Huge or inconsistent selection ranges are rejected against the available
  choices before a set is allocated, and absurdly long numbers produce a
  selection error instead of an uncaught `ValueError`.
- Nested mount points, including same-device bind mounts, are skipped by default
  when discovered below a scan root; explicitly selected nested roots are still
  scanned.

### Security

- Permanent personal-file deletion fails closed. A mode-0700 quarantine owned
  by the same user is not a security boundary against another process running
  as that user, so the public API refuses before any rename or unlink rather
  than delete a replacement inode or report false success. The cleanup engine
  rejects a `permanent_personal` plan before authorization and filesystem work;
  the CLI never offers a permanent-deletion prompt.
- Personal files are Trash-only through `gio trash --`, with snapshot
  revalidation immediately before the handoff. The final-component same-UID
  pathname race is an accepted, recoverable limitation, not a claimed fix.
  Missing or failing Gio never triggers a permanent-delete fallback.
- Cache cleanup uses descriptor-relative, no-follow operations. Cache targets
  are opened through verified directory descriptors. Changed paths, inodes,
  sizes, mtimes, or ctimes abort before removal.
- Cache traversal verifies each child directory descriptor and rejects different
  filesystems and mount-ID boundaries, including same-device bind mounts. It
  fails closed when descriptor or mount-boundary verification is unavailable.
- Scanner traversal reopens queued directories with `O_NOFOLLOW`, verifies
  device/inode identity before enumeration, and uses descriptor-relative entry
  stats so a parent swap cannot redirect a scan.
- Audit logs use a descriptor-relative private `0700` directory and `0600`
  JSON-lines file. Existing `0755`/`0711` directories are tightened;
  group/world-writable, foreign-owned, symlinked, hard-linked, and non-regular
  audit paths are refused. An audit-write failure never blocks reporting.
- `--refresh-metadata` is an explicit side effect. AppSweep warns before DNF
  discovery, uses `--cacheonly` by default, ignores the flag with an
  explanatory warning when package discovery is disabled, and distinguishes
  “no cleanup actions” from “metadata may have been refreshed” in report-only
  and dry-run output.
- Interactive mode still requires both standard input and standard output to be
  terminals unless the user explicitly passes `--interactive`.
- Cache-root overrides remain restricted to Flatpak per-app cache directories;
  sensitive home trees and Flatpak app data/configuration remain protected.

### Changed

- Renamed the Python package, source launcher, console command, and audit state
  directory to the `appsweep` spelling so the implementation matches the
  **AppSweep** product name. Existing audit state is not migrated automatically.
- Personal-file cleanup is now explicitly Trash-only. Permanent deletion is
  disabled rather than documented as protected by a same-UID quarantine.
- Documentation now describes the real threat boundary, Trash-only behavior,
  Gio's accepted recoverable race, metadata-refresh semantics, descriptor
  safety checks, append-only audit retention, mount policy, the real-Fedora
  validation checklist, MIT licensing, and the current 185-test baseline.
  The final remediation and publication files are uncommitted in the working
  tree; the temporary validation report remains untracked historical evidence.

## [1.0.0] — initial candidate baseline, not published

The Unreleased section above supersedes this baseline's permanent-deletion
behavior and test count. This section records the earlier candidate state.

### Added

- Read-only Fedora storage scanner with mount discovery, filesystem-boundary
  pruning, symlink reporting without traversal, hardlink accounting,
  permission-error tracking, cancellation, and progress reporting.
- Separate top-50 largest-personal-file report with exact paths, sizes,
  timestamps, conservative file-type descriptions, and stable numbering.
- Numbered personal-file and cleanup selection with single items, comma lists,
  ranges, mixed syntax, `all`, `none`, `cancel`, exclusions, and cleanup group
  words.
- DNF/DNF5 package-cache, autoremove-preview, and old-kernel discovery.
- rpm-ostree metadata cleanup detection.
- Flatpak unused-runtime discovery with conservative estimate labelling.
- User, system, browser, thumbnail, and Flatpak cache discovery.
- Journal, rotated-log, crash-dump, and temporary-file analysis.
- Trash size/entry analysis and explicit emptying.
- SAFE / CAREFUL / REBUILD cleanup groups with plain-language explanations.
- Explicit `CLEAN` confirmation. The earlier candidate also offered a separate
  `DELETE PERMANENTLY` authorization for personal files; the Unreleased section
  disables that path.
- Personal files moved to Trash through `gio`; no silent permanent deletion
  fallback.
- Snapshot revalidation by path, device, inode, size, mtime, owner, and group.
- Symlink-safe cache cleanup and protected-path policy.
- Allowlisted command shapes for package managers and system tools; no shell.
- Per-command `pkexec` approval without running the whole application as root.
- Terminal UI with ANSI rendering and optional Rich rendering.
- JSON and CSV export with private atomic writes and CSV formula neutralization.
- QUICK, FULL, and CUSTOM scan modes.
- Source launcher, `pyproject.toml` packaging, optional Rich extra, and console
  script.
- Automated `unittest` coverage using temporary directories and mocked host
  tools.

### Known limitations

- Real `dnf`, `dnf5`, `rpm-ostree`, `flatpak`, `journalctl`, `rpm`, `pkexec`,
  and `gio` behavior has not yet been validated on a real Fedora host from this
  sandbox. Mocked tests are not a substitute; see `INTEGRATIONS.md`.
- Missing optional tools are reported as capability limits and disable only
  their dependent cleanup categories.
- Full root-filesystem coverage and host Trash behavior remain host-validation
  items.
- Python 3.10 compatibility has not been tested in the current development
  environment.
- Audit logs currently grow as an append-only history; automatic retention is a
  future hardening item.
