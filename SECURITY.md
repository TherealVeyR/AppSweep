# AppSweep Security Policy

AppSweep is a safety-first Fedora storage analyzer and cleanup assistant. It is
designed to prevent accidental cleanup, path confusion, symlink traversal, and
mount-boundary escapes. It is **not** a sandbox and does not claim to defend
against a process that already runs with the same effective user ID as the
person using AppSweep.

## Supported versions

Security fixes are applied to the current development line and the latest
published release, if any. The project is currently at `1.0.0` and has not been
published. There is no guaranteed support window for older versions.

## Reporting a vulnerability

**Do not open a public issue, discussion, or pull request for a security
problem.**

Use the repository's private **Security → Advisories → New draft security
advisory** flow on GitHub. If private advisories are unavailable, contact the
maintainer through the private contact method listed on the repository profile
and ask for a secure channel before sending details.

Please include:

- the AppSweep version (`appsweep --version`) and the exact commit if known;
- the operating system and Python version;
- the command or API call involved;
- a minimal, disposable reproduction (no real personal data);
- the observed result and the result you expected;
- whether the issue requires a same-UID process, a different UID, root, a
  malformed report/export, a real mount, or a specific desktop tool;
- any suggested fix or mitigation.

Do not include real scan reports, audit logs, home-directory paths, package
lists, credentials, or other personal data. Redact paths and replace real files
with synthetic fixtures.

There is no bug bounty. Reports are handled on a best-effort basis. The
maintainer will acknowledge a complete report, investigate, and coordinate a
fix and disclosure when appropriate. Please allow reasonable time before public
disclosure.

## Threat model and safety boundaries

### In scope

- Accidental or unauthorized cleanup, including bypassing the exact plan,
  `CLEAN` confirmation, or allowlisted command-shape checks.
- Path traversal, symlink traversal, parent-directory replacement, and
  replacement by a process with a **different** UID or outside the current
  user's trust boundary.
- Cache cleanup crossing a filesystem or mount boundary, including a
  same-device bind mount, or proceeding without mount-ID verification.
- Report-only or dry-run paths performing cleanup or modifying scanned data.
  The one explicit exception is `--refresh-metadata`, which may update DNF
  repository metadata and is documented below.
- Audit-log path, permission, symlink, hard-link, or append-integrity failures.
- CSV formula injection or terminal control-character injection.
- Export-file permissions or atomic-write failures.
- Packaging that ships the wrong package, entry point, or license.

### Explicitly out of scope

- **A hostile or concurrent process running with the same effective UID.**
  Such a process can already read, modify, move, or delete everything the user
  can access. A mode-0700 directory owned by that user is not a security
  boundary against it.
- **Permanent personal-file deletion.** It is disabled in this release. The
  public API refuses before any rename or unlink. The historical private
  quarantine primitive is not reachable from the CLI, cleanup engine, or
  public API. Reports about that primitive are still useful for a future
  redesign, but it is not a shipped deletion feature.
- **The Gio Trash handoff is not race-free.** Gio resolves a pathname. A
  same-UID process can swap the final path component between AppSweep's
  revalidation and Gio's resolution. The wrong file can be moved to Trash, but
  it is not permanently deleted and can be restored. This is an accepted,
  recoverable limitation, not a claimed inode-identity guarantee.
- Running AppSweep as root. The CLI refuses euid 0 by design.
- The behavior of real DNF, DNF5, RPM, rpm-ostree, Flatpak, journalctl,
  pkexec, Gio, polkit, or a real desktop session. Those remain host-validation
  items, not proven properties of this checkout.
- Audit-log retention. The log is append-only and has no automatic rotation;
  growth is a documented maintenance issue, not a data-loss defect.
- Missing optional host tools. AppSweep must degrade to a report-only state,
  not silently claim coverage.

## Important current limitations

### Permanent personal-file deletion is disabled

A same-UID process can replace the entry inside a mode-0700 quarantine after
validation and before `unlink`. The old algorithm could delete the replacement,
report the selected file's size, and leave the selected inode in quarantine.
Adding another `stat` immediately before `unlink` does not fix that race.
Because a wrong-file unlink is irreversible, AppSweep fails closed:

- `SafetyPolicy.remove_personal_file` raises
  `PERMANENT_PERSONAL_DELETION_DISABLED_REASON`;
- `CleanupEngine.execute_plan` rejects `permanent_personal=True`;
- the CLI offers no `DELETE PERMANENTLY` prompt;
- missing or failing Gio never triggers a direct-unlink fallback.

Personal files are moved to Trash only. The historical quarantine code is
retained privately as `_remove_personal_file_quarantine_legacy` for
regression/history and future redesign and must not be exposed.

### `--refresh-metadata` is a deliberate side effect

Without the flag, the DNF autoremove preview uses `--cacheonly`. With the flag
and package discovery enabled, AppSweep may retry without `--cacheonly`, which
can update local repository metadata during a report-only or dry-run run.
AppSweep warns before discovery, ignores the flag with an explanation when
package discovery is disabled, and uses different final text for “no cleanup
actions” and “DNF metadata may have been refreshed.” Real DNF behavior is not
verified in this environment.

### Real-host coverage is incomplete

`real_host_verified` remains `false`. The current development environment is
Python 3.14.9 in a Freedesktop Flatpak runtime, not a Fedora workstation. DNF,
DNF5, RPM, rpm-ostree, Flatpak, pkexec, real mounts, a real desktop Trash,
real polkit prompts, and a Python 3.10 runtime have not been validated here.
Mocked tests are not evidence of real-host behavior. See `INTEGRATIONS.md` for
the manual checklist.

## Safe research guidelines

- Use disposable temporary directories and synthetic files.
- Do not test against another person's data.
- Do not run destructive tests against the host, a real Trash, or a package
  manager.
- Do not attempt to enter or alter quarantine directories.
- Do not use a real bind mount without explicit authorization and a disposable
  fixture.
- Do not include secrets or personal scan data in a report.
- If you discover a real data-loss path, stop, preserve evidence, and report it
  privately.

## Related documentation

- `README.md` — user-facing behavior and limitations.
- `ARCHITECTURE.md` — module boundaries and safety design.
- `INTEGRATIONS.md` — host-tool status and real-Fedora checklist.
- `RELEASE_CHECKLIST.md` — release gates and current decision.
- `PUBLISHING.md` — pre-publication and disclosure workflow.
