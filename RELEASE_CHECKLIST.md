# AppSweep Release Checklist

Use this checklist before calling a build a release. Do not mark an item done
from a Flatpak sandbox when it requires a real Fedora host.

## Code and automated verification

- [ ] Working tree is clean and the intended release commit is checked out.
- [ ] `python3 -m compileall -q appssweep tests` succeeds.
- [ ] `python3 -m unittest discover -v` passes without Rich.
- [ ] The full suite passes in a venv with `rich` installed.
- [ ] `ruff check appssweep tests` passes (optional lint gate).
- [ ] `python3 -m build` or the chosen wheel/sdist build succeeds.
- [ ] A clean virtual environment install of the built package exposes the
      `appssweep` console script.
- [ ] No test performs destructive cleanup outside a temporary directory.

## Safety gates

- [ ] Default invocation is report-only when stdin is not a terminal.
- [ ] Interactive mode requires both stdin and stdout to be terminals unless
      `--interactive` is explicit.
- [ ] `--dry-run` behaves exactly like `--report-only` and never offers cleanup.
- [ ] Root execution is refused.
- [ ] Every executable command uses an argument list, never a shell.
- [ ] Cache cleanup remains symlink-safe and allowlisted.
- [ ] Protected paths and cache-root overrides are rechecked.
- [ ] Personal-file snapshots are revalidated before moving or deleting.
- [ ] Permanent personal deletion requires `DELETE PERMANENTLY`.
- [ ] Trash move uses `gio trash --` and has no permanent-delete fallback.
- [ ] Cancelled scans disable cleanup.
- [ ] Cleanup audit logging writes a private `0600` record and reports failures.
- [ ] Audit logging refuses symlink targets and unsafe state directories.

## Functional verification

- [ ] QUICK scan completes and reports the expected personal folders.
- [ ] CUSTOM scan respects explicit roots, exclusions, and minimum size.
- [ ] FULL scan covers the accessible root filesystem on a real Fedora host.
- [ ] `/home`, `/var`, `/usr`, `/boot`, caches, logs, and Trash are represented
      without nested double counting.
- [ ] Permission failures and skipped mounts are reported visibly.
- [ ] The top-50 table uses exact displayed numbering and resolves selections
      against that exact order.
- [ ] Single, comma, range, mixed, `all`, `none`, `cancel`, and exclusion
      selections work.
- [ ] Invalid, reversed, out-of-range, disabled, duplicate, and empty selections
      are handled predictably.
- [ ] JSON and CSV exports match the displayed report.
- [ ] CSV formula neutralization is active.
- [ ] Exports are mode `0600` and written atomically.

## Real Fedora host validation

Record the Fedora version and tool versions in `PROGRESS.md`.

- [ ] `dnf` or `dnf5` cache cleanup, autoremove preview, and old-kernel cleanup.
- [ ] Running kernel and recovery kernel are excluded from old-kernel removal.
- [ ] `rpm-ostree cleanup -m` behavior on an immutable Fedora deployment.
- [ ] Flatpak unused-runtime discovery and official removal command.
- [ ] `journalctl` vacuum behavior and reclaim reporting.
- [ ] `gio` file-to-Trash behavior, including cross-filesystem cases.
- [ ] `pkexec` one-command approval, denial, and cancellation paths.
- [ ] Full scan runtime and memory on a representative large home directory.
- [ ] Python 3.10 compatibility on a supported Fedora release.

## Branding and documentation

- [ ] User-facing name is exactly **AppSweep** everywhere.
- [ ] Lowercase `appssweep` is used only for package, module, command, and
      repository identifiers.
- [ ] `README.md` installation and usage instructions are current.
- [ ] `AGENTS.md`, `PROGRESS.md`, `TODO.md`, `ARCHITECTURE.md`, `CHANGELOG.md`,
      and `CONTRIBUTING.md` are current.
- [ ] Known limitations are stated without implying unverified coverage.
- [ ] A project license has been selected before public distribution.

## Repository and publication

- [ ] `git status` is clean after the final verification run.
- [ ] No secrets, credentials, personal scan data, audit logs, reports, build
      artifacts, or virtual environments are tracked.
- [ ] `../taskvault` remains untouched.
- [ ] No history was rewritten.
- [ ] Explicit permission was obtained before pushing, tagging, publishing, or
      creating a GitHub release.
