# AppSweep Release Checklist

Use this checklist before calling a build a release. Do not mark an item done
from a Flatpak sandbox when it requires a real Fedora host.

## Code and automated verification

- [ ] Working tree is clean and the intended release commit is checked out.
      This final remediation is uncommitted by instruction, and
      `INDEPENDENT_VALIDATION_REPORT.md` is ignored historical evidence.
- [x] `python3 -m compileall -q appsweep tests` succeeds.
- [x] `python3 -m unittest discover -v` passes without Rich
      (184 passed, 1 Rich-only skip, 185 total).
- [ ] The current 185-test suite in a venv with `rich` installed. A previous
      175-test Rich run passed, but the temporary venv is now unavailable.
- [ ] `ruff check appsweep tests` passes (optional lint gate). A previous tree
      passed; Ruff is not installed in the current environment.
- [x] Wheel/sdist packaging configuration is declared in `pyproject.toml`,
      including the MIT license and `LICENSE` file.
- [x] Re-run a wheel build on a disposable copy of the final source and inspect
      the archive for `appsweep/`, not `appssweep/`. `appsweep-1.0.0-py3-none-any.whl`
      contains `appsweep/`, the MIT license, and the console entry point.
      `build`/`twine` sdist checks remain blocked.
- [x] Install the final-source built wheel in a clean virtual environment and
      verify the `appsweep` console script; `appsweep --version` reports
      `appsweep 1.0.0`, a report-only smoke scan passes, and
      `pip uninstall -y appsweep` removes the console script and import.
- [x] A GitHub Actions compile/unittest matrix exists for Python 3.10–3.14.
      It has not been executed on GitHub from this environment.
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
- [x] Personal-file cleanup is Trash-only. Permanent personal-file deletion is
      disabled because a same-UID process can replace a quarantine entry; the
      public safety API, cleanup engine, and CLI fail closed before any rename or
      unlink.
- [x] No `DELETE PERMANENTLY` prompt is offered and no permanent-delete
      fallback exists when Gio is missing or fails.
- [x] The historical private quarantine primitive is retained only for
      regression/history and is not called by application code.
- [x] Deterministic race regressions cover cache target/nested-directory swaps,
      personal parent swaps, selected-file identity changes, legacy
      final-unlink replacements, symlink/directory replacements, and same-size
      edits with restored mtimes. A separate public same-UID substitution test
      proves permanent deletion refuses before any destructive syscall.
- [x] Gio Trash handoff is revalidated immediately before `gio trash --`; a
      changed file is refused. The remaining same-UID final-component race is
      documented as an accepted, recoverable limitation, not as fixed.
- [x] Scanner reopens queued directories with `O_NOFOLLOW`, verifies
      device/inode identity, and enumerates incrementally.
- [x] Protected paths and cache-root overrides are rechecked.
- [x] Personal-file snapshots are revalidated before moving to Trash.
- [x] Trash move uses `gio trash --` and has no permanent-delete fallback.
- [x] Report-only and dry-run messaging distinguishes “no cleanup actions” from
      “DNF metadata may have been refreshed”; `--refresh-metadata` is ignored
      with a warning when package discovery is disabled.
- [x] Cancelled scans disable cleanup.
- [x] Cleanup audit logging writes a private `0600` record and reports failures.
- [x] Existing `0755`/`0711` audit directories are tightened to `0700`;
      group/world-writable, foreign-owned, symlinked, hard-linked, and
      non-regular audit paths are refused.

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
- [x] Hard-link storage is counted once globally, while personal eligibility is
      evaluated for every pathname. Both scan-root orders are covered, multiple
      eligible aliases use one deterministic representative, and a same-size
      alias update cannot evict an unrelated personal file.
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
- [ ] `gio` file-to-Trash behavior, including cross-filesystem cases, and a
      disposable test of the accepted same-UID final-component race. Confirm the
      wrong file remains recoverable from Trash and inspect the metadata.
- [ ] Confirm the CLI/engine never offers or executes permanent personal-file
      deletion on a real host.
- [ ] `pkexec` one-command approval, denial, and cancellation paths.
- [ ] Full scan runtime and memory on a representative large home directory.
- [ ] Python 3.10 compatibility on a supported Fedora release.

## Branding and documentation

- [x] User-facing name is exactly **AppSweep** everywhere.
- [x] Lowercase `appsweep` is used only for package, module, command, and
      repository identifiers.
- [x] `README.md` installation and usage instructions are current.
- [x] `AGENTS.md`, `PROGRESS.md`, `TODO.md`, `ARCHITECTURE.md`, `INTEGRATIONS.md`,
      `CHANGELOG.md`, `CONTRIBUTING.md`, `PUBLISHING.md`, and `README.md` are
      current.
- [x] Security boundaries and unresolved host limitations are documented in
      `SECURITY.md` and `README.md`.
- [x] Known limitations are stated without implying unverified coverage.
- [x] Mocked host-tool tests are explicitly labelled as mocks in docs and tests.
- [ ] The project license (MIT) has been reviewed by the publisher and approved
      for public distribution. The license file and package metadata are in
      place.
- [x] A publication guide with preflight, Git, GitHub, wheel, and release steps
      is included as `PUBLISHING.md`.

## Repository and publication

- [ ] `git status` is clean after the final verification run. The final
      remediation remains uncommitted by instruction; the historical validation
      report is ignored.
- [x] No secrets, credentials, personal scan data, audit logs, reports, build
      artifacts, or virtual environments are tracked.
- [x] `../taskvault` remains untouched.
- [x] No history was rewritten.
- [ ] Explicit permission was obtained before pushing, tagging, publishing, or
      creating a GitHub release.

## Current release decision

**Not release-ready as a fully host-validated Fedora release.** The source tree
is suitable for public review only after the working-tree, wheel, and Git
preflight steps in `PUBLISHING.md` are completed. Do not label it “Fedora
validated”, do not set `real_host_verified` to true, and do not publish a stable
release tag until the real-Fedora checklist above is completed. Permanent
personal-file deletion remains disabled and Gio remains blocked for real-host
verification.
