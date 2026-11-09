# AppSweep Release Checklist

Use this checklist before calling a build a release. Do not mark an item done
from a Flatpak sandbox when it requires a real Fedora host.

## Code and automated verification

- [ ] Working tree is clean and the intended release commit is checked out.
      The remediation is committed on `main`; the temporary
      `INDEPENDENT_VALIDATION_REPORT.md` remains untracked.
- [x] `python3 -m compileall -q appssweep tests` succeeds.
- [x] `python3 -m unittest discover -v` passes without Rich
      (165 passed, 1 Rich-only skip, 166 total).
- [x] The full suite passes in a venv with `rich` installed (166/166).
- [x] `ruff check appssweep tests` passes (optional lint gate).
- [x] Wheel/sdist packaging configuration is declared in `pyproject.toml`.
- [ ] Re-run `python3 -m build` or `pip wheel --no-deps .` on the final source.
      An earlier revision built successfully, but the current available Python
      environments lack `setuptools.build_meta`/`build`. Packaging configuration
      was not changed by the capability work.
- [ ] Install the final-source built package in a clean virtual environment and
      verify the `appssweep` console script.
- [x] No test performs destructive cleanup outside a temporary directory.

## Safety gates

- [x] Default invocation is report-only when stdin is not a terminal.
- [x] Interactive mode requires both stdin and stdout to be terminals unless
      `--interactive` is explicit.
- [x] `--dry-run` behaves exactly like `--report-only` and never offers cleanup.
- [x] Root execution is refused.
- [x] Every executable command uses an argument list, never a shell.
- [x] Cache cleanup remains symlink-safe and allowlisted.
- [x] Cache cleanup uses descriptor-relative no-follow traversal, final
      path/descriptor identity checks, and mount-ID boundary checks. It fails
      closed if those checks are unavailable.
- [x] Permanent personal unlink uses an opened parent descriptor and validates
      the complete file snapshot immediately before `unlink(..., dir_fd=...)`.
- [x] Deterministic race regressions cover cache target/nested-directory swaps,
      personal parent swaps, and selected-file identity changes. Outside victims
      remain unchanged; authorized cleanup still works.
- [x] Scanner reopens queued directories with `O_NOFOLLOW`, verifies
      device/inode identity, and enumerates incrementally.
- [x] Protected paths and cache-root overrides are rechecked.
- [x] Personal-file snapshots are revalidated before moving or deleting.
- [x] Permanent personal deletion requires `DELETE PERMANENTLY`.
- [x] Trash move uses `gio trash --` and has no permanent-delete fallback.
- [x] Cancelled scans disable cleanup.
- [x] Cleanup audit logging writes a private `0600` record and reports failures.
- [x] Audit logging refuses symlink targets, hard links, and unsafe state
      directories.

## Functional verification

- [x] QUICK scan completes and reports the expected personal folders.
- [x] CUSTOM scan respects explicit roots, exclusions, and minimum size.
- [ ] FULL scan covers the accessible root filesystem on a real Fedora host.
- [ ] `/home`, `/var`, `/usr`, `/boot`, caches, logs, and Trash are represented
      without nested double counting.
- [ ] Permission failures and skipped mounts are reported visibly.
- [ ] An authorized real same-device bind mount is skipped by default and
      scanned when explicitly selected as a root; cache cleanup refuses to
      cross it. Current coverage is simulated, not real-mount evidence.
- [x] The top-50 table uses exact displayed numbering and resolves selections
      against that exact order.
- [x] Single, comma, range, mixed, `all`, `none`, `cancel`, and exclusion
      selections work.
- [x] Invalid, reversed, out-of-range, oversized, disabled, duplicate, and
      empty selections are handled predictably.
- [x] JSON and CSV exports match the displayed report.
- [x] CSV formula neutralization is active.
- [x] Exports are mode `0600` and written atomically.
- [x] Missing optional host tools do not abort the scan or unrelated cleanup.
- [x] `HOST INTEGRATIONS` reports available/missing tools and actionable limits.
- [x] JSON capability status says `real_host_verified: false` until host
      validation is recorded.
- [x] Capability detection only checks PATH and executes no host command.

## Real Fedora host validation

Follow the detailed manual checklist in `INTEGRATIONS.md`, using a disposable
Fedora VM or test account. Record the Fedora version, kernel, tool versions, and
evidence in `PROGRESS.md` and the `INTEGRATIONS.md` sign-off table. Do not mark
these items from mocked tests.

- [ ] `HOST INTEGRATIONS` matches `command -v` for the optional host tools.
- [ ] Missing tools are recorded as N/A and do not abort the scan.
- [ ] `dnf` or `dnf5` cache cleanup, autoremove preview, and old-kernel cleanup.
- [ ] `rpm` read-only metadata query; an RPM failure is reported without a crash.
- [ ] Running kernel and recovery kernel are excluded from old-kernel removal.
- [ ] `rpm-ostree cleanup -m` behavior on an immutable Fedora deployment.
- [ ] Flatpak unused-runtime discovery and official removal command.
- [ ] `journalctl` short/IEC size parsing, vacuum behavior, and reclaim reporting.
- [ ] `gio` file-to-Trash behavior, including cross-filesystem cases.
- [ ] `pkexec` one-command approval, denial, and cancellation paths.
- [ ] Full scan runtime and memory on a representative large home directory.
- [ ] Python 3.10 compatibility on a supported Fedora release.

## Branding and documentation

- [x] User-facing name is exactly **AppSweep** everywhere.
- [x] Lowercase `appssweep` is used only for package, module, command, and
      repository identifiers.
- [x] `README.md` installation and usage instructions are current.
- [x] `AGENTS.md`, `PROGRESS.md`, `TODO.md`, `ARCHITECTURE.md`, `INTEGRATIONS.md`,
      `CHANGELOG.md`, and `CONTRIBUTING.md` are current.
- [x] Known limitations are stated without implying unverified coverage.
- [x] Mocked host-tool tests are explicitly labelled as mocks in docs and tests.
- [ ] A project license has been selected before public distribution.

## Repository and publication

- [ ] `git status` is clean after the final verification run. The remediation
      is committed; the temporary independent validation report remains
      untracked.
- [x] No secrets, credentials, personal scan data, audit logs, reports, build
      artifacts, or virtual environments are tracked.
- [x] `../taskvault` remains untouched.
- [x] No history was rewritten.
- [ ] Explicit permission was obtained before pushing, tagging, publishing, or
      creating a GitHub release.
