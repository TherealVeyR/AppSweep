# AppSweep

**See what is using your disk. Clean it up.**

AppSweep is a safety-first storage analyzer and cleanup assistant for Fedora.
It scans the live filesystem, shows where space is going, lists the 50-ish largest
personal files, and explains every cleanup opportunity before anything is
changed.

Scanning is read-only. AppSweep does not delete, move, or modify a file during
a scan. Cleanup only happens after you select exact items and type `CLEAN` in
the confirmation prompt.

Personal files are **Trash-only** in this release. Permanent deletion is
disabled because AppSweep cannot establish a real security boundary between its
deletion quarantine and another process running as the same user. Moving files
to Trash remains recoverable, but it inherits a documented same-UID pathname
race from Gio; see [Security and threat model](#security-and-threat-model).

## What it does

- Measures disk use from the real filesystem, not a stale cache.
- Scans `/home`, `/var`, `/usr`, caches, logs, Trash, package data, and other
  local storage, including separately mounted system paths and a read-only
  `/usr` image on immutable Fedora systems.
- Skips virtual filesystems (`/proc`, `/sys`, `/dev`, `/run`, tmpfs), network
  shares, and external mounts during a full scan. `/tmp` and `/var/tmp` are
  still examined for report-only temporary-file findings.
- Shows the **50 largest personal files** with exact path, file name, exact
  byte size, type, modified date, and a conservative description. Hard-link
  aliases count once toward storage and use one deterministic pathname in the
  ranking.
- Groups cleanup opportunities into **SAFE**, **CAREFUL**, and **REBUILD**.
- Reports permission problems and deliberately skipped paths instead of
  pretending the scan was complete.
- Previews Trash size, top-level count, and example contents before emptying.
- Keeps a separate numbered list for personal files and cleanup categories, so
  a number can never point at the wrong thing.
- Supports QUICK, FULL, and CUSTOM scan modes.
- Exports the exact report you saw as JSON or CSV.
- Records confirmed cleanup plans and results in a private, append-only audit
  log.
- Supports `--dry-run` as an explicit alias for `--report-only`.
- Shows a `HOST INTEGRATIONS` section so missing optional Fedora tools are
  visible before you choose a cleanup category.
- Never runs a shell. Cleanup commands are allowlisted and validated.
- Performs cache cleanup through verified directory descriptors and mount-ID
  checks. Personal files are moved to Trash only in this release; permanent
  personal-file deletion is disabled because AppSweep cannot isolate its
  quarantine from another process running as the same user.
- Refuses to run the whole program as root. Individual system commands request
  administrator approval through `pkexec` only when they are needed.

## Project documentation

- `AGENTS.md` — development rules and mandatory safety invariants.
- `PROGRESS.md` — completed work and verification evidence.
- `TODO.md` — outstanding tasks, risks, and known bugs.
- `ARCHITECTURE.md` — module map, data flow, and technical decisions.
- `INTEGRATIONS.md` — implemented host integrations, mocked coverage, and the
  manual real-Fedora validation checklist.
- `CHANGELOG.md` — notable changes.
- `CONTRIBUTING.md` — development, testing, and Git workflow.
- `RELEASE_CHECKLIST.md` — release gates and host-validation evidence.
- `SECURITY.md` — threat model, same-UID boundary, and vulnerability reporting.
- `PUBLISHING.md` — detailed pre-publication and Git/GitHub release guide.
- `LICENSE` — MIT license terms.
- `temp_task.md` — current work-session scratch state.

Start with `AGENTS.md` before changing code. Update `PROGRESS.md`, `TODO.md`, and
`CHANGELOG.md` after each major milestone.

## Requirements

- Fedora or another modern Linux desktop.
- Python 3.10 or newer.
- No required Python packages. [Rich](https://github.com/Textualize/rich) is
  optional and improves table rendering when installed.
- Optional host tools, used only when present:
  - `dnf` or `dnf5` for package-cache and old-kernel cleanup
  - `rpm-ostree` for immutable Fedora systems
  - `rpm` for read-only old-kernel package metadata
  - `flatpak` for unused runtime cleanup
  - `journalctl` for journal cleanup
  - `gio` for moving files to the desktop Trash
  - `pkexec` for individually approved system commands

AppSweep checks PATH for these tools without executing them. Missing optional
tools disable only their dependent cleanup categories; scanning, reporting, and
unrelated cleanup still work. The `HOST INTEGRATIONS` section explains each
missing tool. See [`INTEGRATIONS.md`](INTEGRATIONS.md) for the implementation
matrix and the manual real-Fedora checklist. A missing tool is not an AppSweep
failure.

## Install

### Run from this source checkout

```bash
cd appsweep
./bin/appsweep --mode quick
```

The launcher works without installing anything.

### Install into a virtual environment

```bash
cd appsweep
python3 -m venv .venv
source .venv/bin/activate
pip install .
appsweep --mode quick
```

For the optional Rich interface:

```bash
pip install ".[rich]"
```

### Install as a user application

```bash
pipx install .
```

## Scan modes

```bash
# Default: common personal folders (Desktop, Documents, Downloads, Videos,
# Pictures, Music, Public, Templates)
appsweep --mode quick

# Full local installation: /home plus system data, keeping separate local
# filesystems and skipping virtual, network, and external mounts
appsweep --mode full

# Custom paths only; repeat --path for more than one
appsweep --mode custom --path ~/Downloads --path /var/tmp
```

A full scan can take a while. Press `Ctrl+C` to stop; the partial report is
kept, but cleanup choices are disabled because coverage is incomplete.

Useful options:

```bash
--min-size 100M            # smallest personal file to rank (default 1M)
--categories cache,logs    # discovery subset; default all
--categories none          # storage report and top files only
--exclude ~/Videos/raw     # leave a path out of the scan; repeatable
--report-only              # never offer cleanup
--dry-run                  # alias for --report-only; never offers cleanup
--interactive              # allow prompts even when stdin is not a TTY
--refresh-metadata         # allow DNF to update repository metadata during package discovery;
                           # may write to the DNF cache even in report-only runs
--json report.json
--csv report.csv
--no-color
--version
```

Non-interactive runs are report-only unless `--interactive` is supplied. This
keeps pipes and scripts safe.

`--refresh-metadata` is an explicit opt-in to a side effect. Without it, the
DNF autoremove preview uses `--cacheonly` and does not attempt a network
metadata refresh. With it, AppSweep prints a warning and the final report says
“No cleanup actions were performed” rather than “No changes were made,” because
DNF metadata may have been refreshed. The flag is ignored with an explanatory
warning when package discovery is disabled. `--dry-run` remains a report-only
run, but if you combine it with `--refresh-metadata`, the metadata warning still
applies.

## The three cleanup groups

| Group | Meaning | Examples |
| --- | --- | --- |
| **SAFE** | Temporary by-products. Apps may rebuild them. No installed app or personal work is removed. | App caches, package-manager cache, rotated logs, journal data, temporary files |
| **CAREFUL** | Your files or Trash. Nothing happens without a separate, explicit confirmation. | The 50 largest personal files, emptying Trash |
| **REBUILD** | Data that can be downloaded or installed again. Requires package-manager action. | Unused Flatpak runtimes, old kernels, unneeded dependencies, rpm-ostree metadata |

Every numbered cleanup item shows:

- what it is
- where it is
- how much space it uses (exact or clearly marked as an estimate)
- why it appears in the report
- what happens if it is cleaned
- exactly how AppSweep will clean it

Items that cannot be cleaned safely are shown as **NOT SELECTABLE** with the
reason.

## Selecting files and cleanup items

The large-file table is separate from the cleanup categories. After the report:

1. Choose personal files.
2. Choose cleanup items.
3. Review the combined plan.
4. Type `CLEAN` to carry it out.

Selection syntax:

```text
1                 one item
1,3,5             several items
1-10              a range
all               every eligible item
none              choose nothing
1,3-5,9           mixed
all, !4           everything except item 4
safe              every eligible SAFE cleanup item
careful           every eligible CAREFUL cleanup item
rebuild           every eligible REBUILD cleanup item
cancel            stop without changes
```

Group words apply to cleanup items, not personal files. Invalid numbers,
reversed ranges, disabled items, and mixed `cancel`/`none` input are rejected
without guessing.

## What happens to personal files

Selected personal files are moved to the desktop Trash with `gio`. This is
deliberate: a mistaken selection can be restored.

Moving a file to Trash **does not free disk space** until Trash is emptied.
The report says this in the table and at the confirmation step.

**Permanent personal-file deletion is disabled in this release.** AppSweep
cannot create a genuine security boundary between a mode-0700 quarantine owned
by your user and another process running as the same user. A same-UID process
can replace the quarantined directory entry after AppSweep validates it and
before the final unlink; adding another `stat` immediately before `unlink` does
not fix that race. Rather than delete the wrong file or report a false success,
the public deletion operation fails closed before it moves or unlinks anything.
No `DELETE PERMANENTLY` prompt is offered, and there is no permanent-deletion
fallback from a failed Trash operation.

Before any personal file is changed, AppSweep re-checks its path, owner, device,
inode, size, modification time, and change time against the scan snapshot. If
any selected file fails that initial check, the whole plan stops before changes
are made. Moving to Trash then revalidates the file immediately before the
`gio trash -- <path>` handoff. Gio resolves the pathname itself, so a hostile or
concurrent same-UID process can still swap the final path component during that
handoff. This is an accepted, recoverable limitation: the wrong file may be
moved to Trash, but it is not permanently deleted and can be restored. It is
**not** a race fix, and real Gio behavior remains unverified until the
disposable-desktop check in `INTEGRATIONS.md` is completed.

The historical private-quarantine algorithm remains in the source only as a
private, regression-tested legacy primitive for future redesign. It is not
reachable from the CLI, cleanup engine, or public safety API. See
[`SECURITY.md`](SECURITY.md) for the full threat model.

## Confirmation and execution

The final plan is displayed again with totals. Nothing runs until you type
`CLEAN`.

Cleanup commands run one at a time and are individually validated. System
commands ask for administrator approval one command at a time with `pkexec`.
If one operation fails, it is reported; nothing is retried automatically.

Reported reclaim is the size of removed data. The free-space figure before and
after is measured on the scan's primary filesystem and is authoritative for
that filesystem, because compression, reflinks, shared extents, and Trash can
make the two differ.

## Exporting a report

```bash
appsweep --mode quick --json report.json
appsweep --mode full --csv report.csv
appsweep --mode custom --path ~/Downloads --json report.json --csv report.csv
```

- JSON contains the complete scan summary, cleanup items, personal files,
  notes, warnings, overlaps, report-only findings, package-manager details, and
  host capability status. This release records `real_host_verified: false`;
  only recorded evidence from the checklist in `INTEGRATIONS.md` can justify
  changing that status.
- CSV uses one row per record with a `record_type` column:
  `personal_file`, `cleanup_item`, or `report_only`.
- Exports are written atomically with mode `0600` (private to your user).
- CSV text that could be interpreted as a spreadsheet formula is neutralized.

## Cleanup audit log

Confirmed cleanup attempts are recorded in a private, append-only audit log:

```text
$XDG_STATE_HOME/appsweep/cleanup-audit.log
```

If `XDG_STATE_HOME` is unset, AppSweep uses:

```text
~/.local/state/appsweep/cleanup-audit.log
```

The state path uses the `appsweep` spelling. Existing state files under earlier
paths are not migrated automatically. The directory is created with mode `0700`
and the log with mode `0600`. An existing `0755` or `0711` audit directory is
tightened to `0700` before the record is written; a group/world-writable
directory, a foreign-owned directory, a symlink, or a hard-linked log file is
refused. Each JSON line records the plan, authorization summary, result, and
refusal errors.

If the audit log cannot be written, AppSweep shows a warning and still reports
the cleanup result. The log is not rotated automatically; remove or archive it
yourself if the history becomes large. Scan reports are not written to this log.

## Safety design

- **Read-only scanning.** No file is opened for writing during a scan.
- **No shell.** Commands are argument lists; `shell=True` is never used.
- **Allowlisted actions.** Every cleanup operation has a dedicated validated
  implementation. Arbitrary commands and paths are never accepted from input.
- **No broad recursive deletion.** Cache removal is narrowly scoped and uses
  descriptor-relative, no-follow traversal. It refuses different filesystems
  and mount-ID boundaries, including same-device bind mounts. System cleanup is
  delegated to the official package tool.
- **No app-wide root.** AppSweep refuses to run as root. `pkexec` wraps only
  the specific system command that needs approval.
- **Protected paths.** Top-level cache folders, home configuration, SSH keys,
  GPG data, Trash internals, and system mount boundaries are protected from
  broad cleanup.
- **Symlinks are never followed.** Directories are opened with
  `O_DIRECTORY | O_NOFOLLOW`; symlinks are counted and reported as entries.
- **Snapshot revalidation.** Numbered selections cannot silently point at a
  different file after the scan. Personal files are checked by path, owner,
  device, inode, size, mtime, and ctime before cleanup; a same-size in-place
  edit with a restored mtime is rejected by the change-time check.
- **Descriptor-relative cache cleanup.** Cache removal uses stable directory
  descriptors and `dir_fd` operations instead of re-resolving an untrusted path
  at the moment of removal. It refuses different filesystems and mount-ID
  boundaries, including same-device bind mounts.
- **Trash-only personal cleanup.** Personal files are revalidated and handed to
  Gio for recoverable Trash movement. Permanent deletion fails closed because a
  same-UID quarantine is not a security boundary. Missing Gio never triggers a
  permanent-delete fallback.
- **Cleanup audit logging.** Confirmed plans and results are written to a
  private append-only JSON-lines log under a `0700` directory with a `0600`
  file. Existing `0755`/`0711` directories are tightened; writable shared
  directories are refused.
- **Dry-run by default.** `--report-only` and `--dry-run` never offer cleanup.
- **Terminal escaping.** Control characters in file names and paths are
  escaped before display.
- **CSV formula protection.** Untrusted text is neutralized for spreadsheets.
- **Cancelled scans disable cleanup.** A partial report remains available for
  inspection and export.

## Security and threat model

AppSweep is designed to prevent accidental cleanup, path confusion, and
replacement by actors outside the current user's trust boundary. It does **not**
claim protection against a hostile or concurrent process running with the same
effective UID. Such a process can already read, modify, move, or delete every
file the user can access, and an unprivileged application cannot create a
security boundary against it.

Specific consequences:

- **Permanent personal-file deletion is disabled.** A mode-0700 quarantine
  owned by the same user is not a boundary. AppSweep refuses the operation
  before moving or unlinking anything rather than claim inode-identity
  protection it cannot provide.
- **Trash movement is recoverable but not race-free.** Gio accepts a pathname.
  A same-UID final-component swap can redirect the handoff to a different file.
  The affected file is moved to Trash, not permanently deleted, and can be
  restored. AppSweep revalidates immediately before the handoff and never falls
  back to permanent deletion, but it does not claim to close this race.
- **Cross-UID, symlink, parent-directory, and mount-boundary attacks** are
  handled with `lstat`, `O_NOFOLLOW`, descriptor-relative traversal, snapshot
  revalidation, and mount-ID checks where the platform provides them. If the
  required primitives are unavailable, the operation fails closed.
- **Real-host verification is incomplete.** DNF, DNF5, RPM, rpm-ostree,
  Flatpak, pkexec, Gio, real mounts, and Python 3.10 have not been validated on
  a disposable Fedora host in this environment. `real_host_verified` remains
  `false`.

Read [`SECURITY.md`](SECURITY.md) for reporting instructions and the complete
boundary statement. Read [`INTEGRATIONS.md`](INTEGRATIONS.md) for the manual
Fedora validation checklist.

## Limitations

- AppSweep reports logical file sizes. Btrfs compression, reflinks, shared
  extents, and sparse files can make physical disk use lower.
- Running inside a Flatpak sandbox limits the scan to accessible paths and may
  hide host package tools. AppSweep warns when it detects this.
- DNF, Flatpak, rpm-ostree, `journalctl`, and `gio` behavior depends on the
  versions installed on the host.
- Flatpak 1.18 does not provide a supported non-interactive dry-run for unused
  runtimes. AppSweep labels that size as an estimate and delegates the actual
  removal to the official `flatpak uninstall --unused` command.
- Old-kernel cleanup keeps the running kernel plus a recovery kernel and lets
  DNF perform the removal.
- Emptying Trash frees space immediately, but moving new files to Trash
  afterwards does not. The confirmation summary explains the order.
- The cleanup audit log is append-only and has no automatic rotation yet.
- Permanent personal-file deletion is disabled. The historical
  `.appsweep-quarantine-*` algorithm is retained only as a private legacy
  primitive and is not reachable from the product. Do not manually create or
  rely on quarantine directories for deletion.
- Moving a personal file to Trash still uses Gio's pathname interface. A
  same-UID final-component swap during the `gio trash` handoff is an accepted,
  recoverable limitation: the wrong file can be moved to Trash, but not
  permanently deleted. Real-Gio behavior remains **BLOCKED** pending a
  disposable Fedora desktop test.
- Missing optional host tools are reported as capability limits, not as
  application failures. Dependent cleanup categories are skipped.
- Directory cache cleanup refuses to run if descriptor-relative operations or
  `/proc/self/fdinfo` mount-ID verification are unavailable; it does not fall
  back to a path-only deletion.
- Real Fedora host tools and real secondary/same-device bind mounts have not
  been validated from this development sandbox. Mount policy uses unit fixtures
  and simulated mount IDs; see `INTEGRATIONS.md` for the manual checklist and
  `RELEASE_CHECKLIST.md` for the release gates.
- The declared Python 3.10 minimum has not been exercised in this environment;
  all recorded results are from Python 3.14.

## Troubleshooting

### A scan reports skipped, inaccessible, or sandboxed paths

That is deliberate. AppSweep does not silently claim full coverage. Permission
errors, virtual/network/external mounts, and Flatpak sandbox limits are listed
with the rest of the scan notes. Run outside the sandbox on the Fedora host for
host-level coverage.

### No package-manager cleanup is offered

Check the `HOST INTEGRATIONS` section. If `dnf`, `dnf5`, and `rpm-ostree` are
all absent, package cleanup is unavailable, but the storage scan and other
categories still work. If DNF is present but `rpm` is absent, old-kernel
cleanup is unavailable; cache cleanup and autoremove preview may still work.
Package transactions are previews and still require explicit confirmation. See
`INTEGRATIONS.md` for the full dependency matrix.

### `gio` is missing, so files cannot be moved to Trash

Install the desktop integration that provides `gio`. AppSweep does not fall back
to permanent deletion when Trash is unavailable. The `HOST INTEGRATIONS`
section reports this limitation before you select a personal file.

### A system command asks for approval and then fails

AppSweep uses `pkexec` for one allowlisted command at a time. Cancelling the
approval prompt or a policy denial is reported without retrying. Check the
polkit/administrator configuration on the host.

### The cleanup audit log is not writable

AppSweep warns and preserves the cleanup result. Check that the state directory
and log are owned by you and that the `appsweep` directory is not
group/world-writable or a symlink. The default path is shown in the warning.

### A full scan takes longer than expected

Full scans traverse the accessible local installation. Use `--mode quick` for
common personal folders, `--exclude` for large trees you do not need, or
`--min-size` to reduce the ranked-file workload. `Ctrl+C` keeps a partial report
but disables cleanup.

### The terminal output is hard to read

Try `--no-color`, set `NO_COLOR=1`, or install the optional Rich extra. Rich is
never required.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Report completed; no changes were requested or all requested actions succeeded |
| `1` | A scan, export, or cleanup operation failed |
| `2` | Invalid command-line usage or unsafe environment (for example, running as root) |
| `130` | Cancelled by the user |

## Development and tests

The test suite uses only temporary directories and mocks. It never performs
destructive host cleanup.

```bash
cd appsweep
python3 -m compileall -q appsweep tests
python3 -m unittest discover -v
```

Coverage includes descriptor-relative traversal, directory-swap and
visible-path replacement races, legacy quarantine regressions, the new
fail-closed permanent-deletion refusal, Gio handoff revalidation and
no-permanent-fallback behavior, symlink handling, permission errors, mount
boundaries, mount-ID checks, global hardlink accounting with personal
eligibility, audit-directory mode enforcement, ctime checks, package and Flatpak
detection, capability detection, bounded selection parsing, authorization,
cancellation callbacks, path safety, storage calculations, overlap detection,
CSV formula neutralization, reporting, cleanup audit-log privacy/refusal, CLI
flows, and the exact top-50 numbering shown during selection. The suite
currently has 185 tests: 184 pass and 1 Rich-only test is skipped without Rich.
The current Rich run and Ruff check are blocked because the temporary Rich
venv/tool is unavailable; the previous Rich-enabled suite passed. `pytest` is
not installed and was not run. Missing host tools and real mount fixtures are
exercised with clearly labelled mocks and simulations; see `INTEGRATIONS.md`
for what is and is not verified.

Run the optional Rich suite with:

```bash
/tmp/opencode/appsweep-rich-venv/bin/python -m unittest discover
```

The repository includes `.github/workflows/tests.yml`, which runs compileall
and the standard-library test suite on Python 3.10 through 3.14. It does not
run real Fedora tools, Gio, pkexec, Ruff, or type checkers. Those remain
manual/host-validation gates.

## Project layout

```text
appsweep/
├── bin/appsweep              source-checkout launcher
├── appsweep/
│   ├── cli.py                 command-line workflow
│   ├── scanner.py             filesystem traversal and ranking
│   ├── analyzer.py            report totals and overlap checks
│   ├── capabilities.py        read-only host-tool capability report
│   ├── categories.py          numbered cleanup assembly
│   ├── cleanup_engine.py      validated execution
│   ├── audit.py               private cleanup audit log
│   ├── safety.py              protected paths and authorization
│   ├── reporting.py           JSON and CSV export
│   ├── ui.py                   terminal presentation
│   └── ...                    discovery modules for packages, Flatpak,
│                              caches, logs, Trash, and system data
├── tests/                     unittest suite
├── .github/workflows/tests.yml CI compile and unittest matrix
├── .gitattributes             repository text/binary normalization
├── pyproject.toml
├── requirements.txt
├── AGENTS.md                  development rules and safety invariants
├── PROGRESS.md                completed work and verification evidence
├── TODO.md                    outstanding work and known limitations
├── ARCHITECTURE.md            module map and design decisions
├── INTEGRATIONS.md            host integration matrix and Fedora checklist
├── CHANGELOG.md               notable changes
├── CONTRIBUTING.md            development, testing, and Git workflow
├── RELEASE_CHECKLIST.md       release gates and host-validation evidence
├── SECURITY.md                 threat model and private reporting guidance
├── PUBLISHING.md               pre-publication and Git/GitHub guide
├── LICENSE                     MIT license text
└── README.md
```

## Uninstall

If installed with `pipx`:

```bash
pipx uninstall appsweep
```

If installed in a virtual environment, remove the environment and the
`appsweep` command. AppSweep keeps no report database, but it does retain the
private cleanup audit history at
`$XDG_STATE_HOME/appsweep/cleanup-audit.log` (or
`~/.local/state/appsweep/cleanup-audit.log`). Remove that file yourself if you
do not want the history to remain. Reports you exported are separate files and
are not removed by uninstalling.

## License

AppSweep is licensed under the MIT License. See [`LICENSE`](LICENSE).

## Contributing and security

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before sending a change. Do not report
security vulnerabilities in a public issue; follow [`SECURITY.md`](SECURITY.md).
