# AppSweep TODO

Last updated: 2026-11-11

Status legend:

- **P0** — blocks safe release.
- **P1** — must be verified or fixed before calling the release complete.
- **P2** — worthwhile hardening, coverage, or polish.
- **P3** — future enhancement.

## Known bugs

No known P0/P1 code bugs remain in the code paths covered by the automated
suite. The older independent validation findings `BUG-001` through `BUG-004`
and the report's `POT-001` through `POT-005` are fixed in the remediation commit
on `main`. A later follow-up review added `BUG-005`, `BUG-006`, and `BUG-007`
plus a ctime hardening check; those fixes are implemented and tested in the
uncommitted working tree. The follow-up labels `POT-001` (ctime), `POT-002`
(Gio Trash pathname race), and `POT-003` (audit retention) are distinct from the
older untracked report's same-numbered findings, whose range, mount, traversal,
cancellation, and final-identity items were already remediated.

The final security audit's same-UID quarantine finding is closed by **failing
closed**: permanent personal-file deletion is disabled because a mode-0700
quarantine is not a boundary against a same-UID process. The CLI offers no
permanent-deletion prompt, the engine rejects a permanent plan before any
filesystem work, and the public safety API refuses before moving or unlinking.
The historical private quarantine primitive remains only for regression/history
and future redesign. Gio Trash movement remains available because it is
recoverable, but its same-UID final-component race is an accepted limitation,
not a fixed issue. The source report `INDEPENDENT_VALIDATION_REPORT.md` still
covers the older baseline, remains untracked, and has not been rewritten.

The uncommitted working tree also renames the top-level project directory,
Python package, launcher, console command, and audit state path to the
`appsweep` spelling. The user-facing product name remains **AppSweep**. The
185-test suite was rerun after the rename and final remediation; existing audit
state is not migrated automatically.

This does not mean the host-tool integrations or real mount behavior have been
proven on a real Fedora installation. A real secondary filesystem or
same-device bind mount was not created in this environment; mount policy uses
mountinfo unit fixtures for the scanner and simulated mount IDs for cache
deletion and legacy quarantine regression tests. See the P1 validation items
below.

## P0 — release blockers

No known P0 items.

## P1 — required before claiming full release coverage

- [x] **Commit the older independent-validation remediation.** Those fixes are
      committed on `main`; `INDEPENDENT_VALIDATION_REPORT.md` remains local
      historical evidence and is ignored for publication.
- [ ] **Review and commit the final security remediation, BUG-005/007, ctime,
      publication files, and `appsweep` rename.** They are deliberately
      uncommitted in the working tree by instruction. Do not commit without
      explicit permission; re-run the full suite and inspect the diff first.
- [ ] **Validate mount behavior with authorized real fixtures.** Create a
      disposable same-device bind mount and a separate-device mount, then
      confirm the scanner prunes them, an explicitly selected root is still
      scanned, and cache cleanup refuses to cross the mount boundary. Current
      coverage is unit fixtures and simulated mount IDs only.
- [ ] **Validate on a real Fedora host with real system tools.** Follow the
      manual checklist in `INTEGRATIONS.md`; mocked tests are not a substitute.
  - [ ] Capability detection: confirm `HOST INTEGRATIONS` matches
        `command -v` for every optional tool, and that missing tools do not
        abort the scan.
  - [ ] `dnf`/`dnf5`: package cache cleanup, autoremove preview, old-kernel
        removal, and running-kernel/recovery-kernel exclusion.
  - [ ] `rpm`: read-only old-kernel metadata query succeeds; an RPM failure is
        reported without crashing discovery.
  - [ ] `rpm-ostree`: `rpm-ostree cleanup -m` only; confirm no deployment or
        rollback mutation is offered.
  - [ ] `flatpak`: unused-runtime discovery and official `uninstall --unused`
        behavior; confirm estimates are clearly labeled.
  - [ ] `journalctl`: vacuum command, short/IEC size parsing, and reclaim
        reporting.
  - [ ] `gio`: file-to-Trash behavior, including files on a different
        filesystem from the home Trash. Evaluate the accepted same-UID
        final-component pathname race with disposable data; confirm the wrong
        file remains recoverable from Trash. Do not claim the race fixed. Real
        Gio remains blocked until this is completed.
  - [ ] Confirm no permanent-deletion prompt is offered and a synthetic
        permanent plan is refused before any rename or unlink.
  - [ ] `pkexec`: one-command-at-a-time approval and failure/cancel handling.

- [ ] **Run a real FULL scan on a normal Fedora installation.**
  - [ ] Confirm `/home`, `/var`, `/usr`, `/boot`, `/opt`, `/etc`, `/root`,
        `/srv`, caches, logs, and Trash are covered without double counting.
  - [ ] Confirm a read-only `/usr` image and the rpm-ostree/Silverblue
        `/var/home` layout are represented on an immutable system.
  - [ ] Confirm `/tmp` temporary-file findings are reported without counting a
        memory-backed tmpfs as disk storage.
  - [ ] Confirm virtual, network, and external mounts are skipped with clear
        reasons.
  - [ ] Confirm permission errors are reported rather than hidden.
  - [ ] Check runtime and memory behavior on a large home directory.
  - [ ] Check the rpm-ostree/Silverblue `/var/home` and read-only `/usr` layout.

- [ ] **Verify audit logging on a real Fedora host.**
  - [ ] Confirm `XDG_STATE_HOME` and the `~/.local/state` fallback produce the
        expected private path.
  - [ ] Confirm a failed audit write warns without hiding a cleanup result.
  - [ ] Confirm started, finished, and refused records survive a host reboot.

- [ ] **Verify Python 3.10 compatibility** on a supported Fedora release. The
      development environment used Python 3.14.

- [ ] **Re-run the full test suite and smoke scans after host validation.**
      Record the real Fedora version, tool versions, and results in
      `PROGRESS.md`.

## P2 — hardening and coverage

- [ ] Report free-space before/after for each affected filesystem when a plan
      spans more than one mount, instead of only the scan's primary filesystem.
- [ ] Add a packaging test that builds a wheel and verifies the console script
      and package contents.
- [ ] **POT-002 — Gio Trash pathname race:** moving a personal file to Trash is
      still a pathname handoff after validation. The same-UID race is an
      accepted, recoverable limitation. A future private-staging design must
      preserve the original name and Trash restore metadata, survive crashes,
      and be tested against real Gio before it is claimed to close the race. No
      fix is claimed in this working tree.
- [ ] **POT-003 — audit retention:** the log remains append-only with no
      automatic rotation, which prevents silent audit-history deletion. Decide
      whether to add a user-visible archival policy; do not add silent truncation
      or automatic deletion.
- [ ] **Permanent-deletion redesign:** any future permanent personal-file
      deletion requires a genuinely separate, narrowly scoped privilege boundary
      or a design that never unlinks a same-UID-replaceable quarantine entry.
      Do not expose the legacy quarantine primitive or add a last-moment `stat`
      as a fix. Add a reviewed helper interface, focused tests, and real-host
      evidence before re-enabling.
- [x] Select a project license. MIT is included in `LICENSE` and `pyproject.toml`;
      the publisher must still approve it before public distribution.
- [x] Add `SECURITY.md` and `PUBLISHING.md` for a publishable repository.

## P3 — future enhancements

- [ ] Use a supported Flatpak dry-run API if a future Flatpak release provides
      one, replacing the conservative estimate.
- [ ] Add a configurable report directory or default export location.
- [ ] Consider a saved scan snapshot mode for offline comparison, with explicit
      stale-data warnings.
- [ ] Consider translations while keeping terminal escaping and width handling
      correct.
- [ ] Run the GitHub Actions compile/unittest matrix on the published
      repository and add Ruff, mypy/pyright, Rich, and Python 3.10 runtime gates
      when those tools are available.
- [ ] Add a wheel-install smoke test to CI if a build backend can be provided
      without weakening the no-network release check.

## Documentation status

- [x] `AGENTS.md` — development rules and safety invariants.
- [x] `PROGRESS.md` — completed work and verification evidence.
- [x] `TODO.md` — this file.
- [x] `ARCHITECTURE.md` — module map and technical decisions.
- [x] `INTEGRATIONS.md` — host integration matrix, mocked coverage, and manual
      real-Fedora validation checklist.
- [x] `README.md` — installation, usage, and setup.
- [x] `CHANGELOG.md` — notable changes.
- [x] `CONTRIBUTING.md` — development, testing, and Git workflow.
- [x] `RELEASE_CHECKLIST.md` — release gates and host-validation evidence.
- [x] `SECURITY.md` — threat model and reporting guidance.
- [x] `PUBLISHING.md` — pre-publication and Git/GitHub guide.
- [x] `LICENSE` — MIT license.
- [x] `temp_task.md` — active session scratch state.

Keep this file current after every major milestone. Move completed items to
`PROGRESS.md` when they are implemented and verified.
