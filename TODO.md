# AppSweep TODO

Last updated: 2026-11-09

Status legend:

- **P0** — blocks safe release.
- **P1** — must be verified or fixed before calling the release complete.
- **P2** — worthwhile hardening, coverage, or polish.
- **P3** — future enhancement.

## Known bugs

No known P0/P1 code bugs remain in the code paths covered by the automated
suite. The independent validation findings `BUG-001` through `BUG-004` and
`POT-001` through `POT-005` are fixed in the remediation commit on `main`. The
fixes and their regressions are recorded in `PROGRESS.md`. The source report
`INDEPENDENT_VALIDATION_REPORT.md` remains untracked as a temporary validation
artifact.

This does not mean the host-tool integrations or real mount behavior have been
proven on a real Fedora installation. A real secondary filesystem or
same-device bind mount was not created in this environment; mount policy uses
mountinfo unit fixtures for the scanner and simulated mount IDs for cache
deletion. See the P1 validation items below.

## P0 — release blockers

No known P0 items.

## P1 — required before claiming full release coverage

- [x] **Commit the independent-validation remediation.** The fixes are
      committed on `main`; `INDEPENDENT_VALIDATION_REPORT.md` remains untracked
      as a temporary validation artifact.
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
        filesystem from the home Trash.
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
- [ ] Add audit-log rotation or a documented retention policy; the current log
      is append-only and grows without a built-in limit.
- [ ] Decide and add a project license before public distribution.

## P3 — future enhancements

- [ ] Use a supported Flatpak dry-run API if a future Flatpak release provides
      one, replacing the conservative estimate.
- [ ] Add a configurable report directory or default export location.
- [ ] Consider a saved scan snapshot mode for offline comparison, with explicit
      stale-data warnings.
- [ ] Consider translations while keeping terminal escaping and width handling
      correct.

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
- [x] `temp_task.md` — active session scratch state.

Keep this file current after every major milestone. Move completed items to
`PROGRESS.md` when they are implemented and verified.
