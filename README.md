# AppSweep

**See what is using your Fedora disk. Choose what happens next.**

AppSweep is a safety-first storage analyzer and cleanup assistant for Fedora.
It scans the live filesystem, shows where space is going, lists the 50 largest
personal files, and explains every cleanup opportunity before anything is
changed.

Scanning is read-only. AppSweep does not delete, move, or modify a file during
a scan. Cleanup only happens after you select exact items and type `CLEAN` in
the confirmation prompt.

## What it does

- Measures disk use from the real filesystem, not a stale cache.
- Scans `/home`, `/var`, `/usr`, caches, logs, Trash, package data, and other
  local storage.
- Skips virtual filesystems (`/proc`, `/sys`, `/dev`, `/run`, tmpfs), network
  shares, and external mounts during a full scan. `/tmp` and `/var/tmp` are
  still examined for report-only temporary-file findings.
- Shows the **50 largest personal files** with exact path, file name, exact
  byte size, type, modified date, and a conservative description.
- Groups cleanup opportunities into **SAFE**, **CAREFUL**, and **REBUILD**.
- Reports permission problems and deliberately skipped paths instead of
  pretending the scan was complete.
- Keeps a separate numbered list for personal files and cleanup categories, so
  a number can never point at the wrong thing.
- Supports QUICK, FULL, and CUSTOM scan modes.
- Exports the exact report you saw as JSON or CSV.
- Records confirmed cleanup plans and results in a private, append-only audit
  log.
- Supports `--dry-run` as an explicit alias for `--report-only`.
- Never runs a shell. Cleanup commands are allowlisted and validated.
- Refuses to run the whole program as root. Individual system commands request
  administrator approval through `pkexec` only when they are needed.

## Project documentation

- `AGENTS.md` — development rules and mandatory safety invariants.
- `PROGRESS.md` — completed work and verification evidence.
- `TODO.md` — outstanding tasks, risks, and known bugs.
- `ARCHITECTURE.md` — module map, data flow, and technical decisions.
- `CHANGELOG.md` — notable changes.
- `CONTRIBUTING.md` — development, testing, and Git workflow.
- `RELEASE_CHECKLIST.md` — release gates and host-validation evidence.
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
  - `flatpak` for unused runtime cleanup
  - `journalctl` for journal cleanup
  - `gio` for moving files to the desktop Trash
  - `pkexec` for individually approved system commands

AppSweep reports what it cannot use. Missing tools do not prevent a scan.

## Install

### Run from this source checkout

```bash
cd appssweep
./bin/appssweep --mode quick
```

The launcher works without installing anything.

### Install into a virtual environment

```bash
cd appssweep
python3 -m venv .venv
source .venv/bin/activate
pip install .
appssweep --mode quick
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
appssweep --mode quick

# Full local installation: /home plus system data, keeping separate local
# filesystems and skipping virtual, network, and external mounts
appssweep --mode full

# Custom paths only; repeat --path for more than one
appssweep --mode custom --path ~/Downloads --path /var/tmp
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
--refresh-metadata         # allow a DNF preview to refresh repository metadata
--json report.json
--csv report.csv
--no-color
--version
```

Non-interactive runs are report-only unless `--interactive` is supplied. This
keeps pipes and scripts safe.

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

Selected personal files are moved to the desktop Trash by default with `gio`.
This is deliberate: a mistaken selection can be restored.

Moving a file to Trash **does not free disk space** until Trash is emptied.
The report says this in the table and at the confirmation step.

Permanent deletion is a separate choice. It requires:

1. answering yes to “Permanently delete these files instead of moving them to
   Trash?”, and
2. typing `DELETE PERMANENTLY`.

If the second phrase is not typed exactly, the files are moved to Trash
instead. AppSweep never silently falls back to permanent deletion.

Before any personal file is changed, AppSweep re-checks its path, device,
inode, size, and modification time against the scan snapshot. If any selected
file fails that initial check, the whole plan stops before changes are made.
A file that changes later is skipped and reported.

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
appssweep --mode quick --json report.json
appssweep --mode full --csv report.csv
appssweep --mode custom --path ~/Downloads --json report.json --csv report.csv
```

- JSON contains the complete scan summary, cleanup items, personal files,
  notes, warnings, overlaps, report-only findings, and package-manager details.
- CSV uses one row per record with a `record_type` column:
  `personal_file`, `cleanup_item`, or `report_only`.
- Exports are written atomically with mode `0600` (private to your user).
- CSV text that could be interpreted as a spreadsheet formula is neutralized.

## Cleanup audit log

Confirmed cleanup attempts are recorded in a private, append-only audit log:

```text
$XDG_STATE_HOME/appssweep/cleanup-audit.log
```

If `XDG_STATE_HOME` is unset, AppSweep uses:

```text
~/.local/state/appssweep/cleanup-audit.log
```

The directory is created with mode `0700` and the log with mode `0600`. Each
JSON line records the plan, authorization summary, result, and refusal errors.
AppSweep refuses to append through a symlink or into an unsafe shared state
directory.

If the audit log cannot be written, AppSweep shows a warning and still reports
the cleanup result. The log is not rotated automatically; remove or archive it
yourself if the history becomes large. Scan reports are not written to this log.

## Safety design

- **Read-only scanning.** No file is opened for writing during a scan.
- **No shell.** Commands are argument lists; `shell=True` is never used.
- **Allowlisted actions.** Every cleanup operation has a dedicated validated
  implementation. Arbitrary commands and paths are never accepted from input.
- **No broad recursive deletion.** Cache removal is narrowly scoped and
  symlink-safe. System cleanup is delegated to the official package tool.
- **No app-wide root.** AppSweep refuses to run as root. `pkexec` wraps only
  the specific system command that needs approval.
- **Protected paths.** Top-level cache folders, home configuration, SSH keys,
  GPG data, Trash internals, and system mount boundaries are protected from
  broad cleanup.
- **Symlinks are never followed.** They are counted and reported.
- **Snapshot revalidation.** Numbered selections cannot silently point at a
  different file after the scan.
- **Cleanup audit logging.** Confirmed plans and results are written to a
  private append-only JSON-lines log.
- **Dry-run by default.** `--report-only` and `--dry-run` never offer cleanup.
- **Terminal escaping.** Control characters in file names and paths are
  escaped before display.
- **CSV formula protection.** Untrusted text is neutralized for spreadsheets.
- **Cancelled scans disable cleanup.** A partial report remains available for
  inspection and export.

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
- Real Fedora host tools have not been validated from this development sandbox;
  see `RELEASE_CHECKLIST.md`.

## Troubleshooting

### A scan reports skipped, inaccessible, or sandboxed paths

That is deliberate. AppSweep does not silently claim full coverage. Permission
errors, virtual/network/external mounts, and Flatpak sandbox limits are listed
with the rest of the scan notes. Run outside the sandbox on the Fedora host for
host-level coverage.

### No package-manager cleanup is offered

Check that `dnf`, `dnf5`, or `rpm-ostree` is installed and available. Missing
tools do not prevent a storage scan, but their cleanup categories are skipped.
Package transactions are previews and still require explicit confirmation.

### `gio` is missing, so files cannot be moved to Trash

Install the desktop integration that provides `gio`. AppSweep does not fall back
to permanent deletion when Trash is unavailable.

### A system command asks for approval and then fails

AppSweep uses `pkexec` for one allowlisted command at a time. Cancelling the
approval prompt or a policy denial is reported without retrying. Check the
polkit/administrator configuration on the host.

### The cleanup audit log is not writable

AppSweep warns and preserves the cleanup result. Check that the state directory
and log are owned by you and that the `appssweep` directory is not
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
cd appssweep
python3 -m compileall -q appssweep tests
python3 -m unittest discover -v
```

Coverage includes traversal, symlink handling, permission errors, mount
boundaries, hardlink accounting, package and Flatpak detection, selection
parsing, authorization, cancellation, path safety, storage calculations,
overlap detection, CSV formula neutralization, reporting, cleanup audit-log
privacy/refusal, CLI flows, and the exact top-50 numbering shown during
selection.

Run the optional Rich suite with:

```bash
/tmp/opencode/appssweep-rich-venv/bin/python -m unittest discover
```

## Project layout

```text
appssweep/
├── bin/appssweep              source-checkout launcher
├── appssweep/
│   ├── cli.py                 command-line workflow
│   ├── scanner.py             filesystem traversal and ranking
│   ├── analyzer.py            report totals and overlap checks
│   ├── categories.py          numbered cleanup assembly
│   ├── cleanup_engine.py      validated execution
│   ├── audit.py               private cleanup audit log
│   ├── safety.py              protected paths and authorization
│   ├── reporting.py           JSON and CSV export
│   ├── ui.py                   terminal presentation
│   └── ...                    discovery modules for packages, Flatpak,
│                              caches, logs, Trash, and system data
├── tests/                     unittest suite
├── pyproject.toml
├── requirements.txt
├── AGENTS.md                  development rules and safety invariants
├── PROGRESS.md                completed work and verification evidence
├── TODO.md                    outstanding work and known limitations
├── ARCHITECTURE.md            module map and design decisions
├── CHANGELOG.md               notable changes
├── CONTRIBUTING.md            development, testing, and Git workflow
├── RELEASE_CHECKLIST.md       release gates and host-validation evidence
└── README.md
```

## Uninstall

If installed with `pipx`:

```bash
pipx uninstall appssweep
```

If installed in a virtual environment, remove the environment and the
`appssweep` command. AppSweep keeps no report database, but it does retain the
private cleanup audit history at
`$XDG_STATE_HOME/appssweep/cleanup-audit.log` (or
`~/.local/state/appssweep/cleanup-audit.log`). Remove that file yourself if you
do not want the history to remain. Reports you exported are separate files and
are not removed by uninstalling.
