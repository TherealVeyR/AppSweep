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

### Security

- Audit logs use a private `0700` directory and `0600` JSON-lines file, refuse
  symlink targets and unsafe state directories, and never block reporting when
  an audit write fails.
- Interactive mode still requires both standard input and standard output to be
  terminals unless the user explicitly passes `--interactive`.
- Cache-root overrides remain restricted to Flatpak per-app cache directories;
  sensitive home trees and Flatpak app data/configuration remain protected.

### Changed

- Documentation now describes cleanup audit logging, dry-run naming, Git
  workflow, host capability limits, the real-Fedora validation checklist, and
  the current test baseline.

## [1.0.0] — release candidate, not published

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
- Explicit `CLEAN` confirmation and separate `DELETE PERMANENTLY` authorization
  for personal files.
- Personal files moved to Trash by default through `gio`; no silent permanent
  deletion fallback.
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
