# AppSweep Host Integrations

This document separates three things that are easy to confuse:

1. **Implemented** — the code path exists and has command-shape or mocked
   integration coverage.
2. **Verified in this environment** — the tool was available and the behavior
   was exercised here.
3. **Verified on a real Fedora host** — a person completed the manual checklist
   below with the real host tools.

Mocked tests are valuable regression coverage, but they are **not** real Fedora
validation. Do not change a row to “verified” based only on a mock.

## Current environment status

Recorded on 2026-11-09 in the development sandbox:

| Tool | Present | Evidence / status |
| --- | --- | --- |
| `dnf` | No | Not available; mocked paths only. |
| `dnf5` | No | Not available; mocked paths only. |
| `rpm-ostree` | No | Not available; mocked paths only. |
| `rpm` | No | Not available; package metadata is mocked only. |
| `flatpak` | No | Not available; mocked paths only. |
| `journalctl` | No | Not available; mocked paths only. |
| `pkexec` | No | Not available; mocked paths only. |
| `gio` | Yes, 2.88.3 | Binary present, but no real host Trash operation was performed. |

### 2026-11-09 validation-remediation note

No host-tool status changed during the independent-validation remediation. The
automated suite grew to 166 tests and now includes descriptor-relative
cache/personal deletion, directory-swap races, scanner reopen checks, global
hardlink accounting, bounded selection ranges, and mount-boundary policy
fixtures. No real `dnf`, `dnf5`, `rpm`, `rpm-ostree`, `flatpak`, `journalctl`,
`pkexec`, or `gio` operation was performed. A real secondary filesystem or
same-device bind mount was not created; scanner mount policy uses mountinfo
fixtures and cache mount checks use simulated mount IDs in unit tests. Those
results are not real-host or real-mount evidence.

Consequences:

- The application still scans and reports where its filesystem permissions
  allow it. Missing tools disable only the cleanup categories that depend on
  them.
- The capability section in the terminal and the `capabilities` object in JSON
  explicitly say that real Fedora execution is unverified.
- A missing tool must be recorded as “not available” or “not applicable” on the
  checklist, not as an AppSweep implementation failure.

## Implementation matrix

| Integration | Implemented behavior | Primary code | Automated coverage | Real Fedora host |
| --- | --- | --- | --- | --- |
| DNF / DNF5 | Detects `dnf`/`dnf5`; measures DNF caches; offers `dnf clean all`; previews autoremove with `--assumeno --cacheonly` by default; optional metadata refresh only with `--refresh-metadata`; removes old kernel packages through DNF with the running kernel excluded. | `appssweep/package_manager.py` | Mocked detection, preview parsing, cacheonly behavior, command shapes, old-kernel selection, and cache/autoremove/old-kernel discovery. | **Not executed.** |
| rpm-ostree | Detects an rpm-ostree system by `/run/ostree-booted` or the binary; offers metadata cache cleanup only. Deployments, rollbacks, and package layering are not modified. | `appssweep/package_manager.py` | Mocked metadata discovery and allowlist rejection of non-metadata commands. | **Not executed.** |
| RPM | Reads `rpm -qa` metadata to identify installed kernel packages and estimate old-kernel removal. It never removes files directly. | `appssweep/package_manager.py` | Mocked old-kernel discovery verifies removal candidates and running-kernel exclusion. | **Not executed.** |
| Flatpak | Detects Flatpak; lists applications and runtimes per user/system installation; estimates unused runtimes; offers only `flatpak uninstall --unused --assumeyes`. Applications and app data are not removed. | `appssweep/flatpak.py` | Mocked runtime parsing, availability fallback, unused-runtime discovery, and command-shape validation. | **Not executed.** |
| journalctl | Measures journal usage with `journalctl --disk-usage`; accepts short and IEC size units; offers `journalctl --vacuum-size=256M`; falls back to read-only directory measurement if usage cannot be read. | `appssweep/log_cleaner.py` | Mocked usage parsing, vacuum discovery, and no-`journalctl` report-only fallback. | **Not executed.** |
| pkexec | Wraps only individual allowlisted system commands with `pkexec`; reports a missing helper without launching the command; never runs the whole application as root. | `appssweep/runner.py`, `appssweep/cleanup_engine.py` | Mocked command wrapping, missing-`pkexec` failure, and command allowlist tests. | **Not executed.** |
| gio | Finds the desktop Trash; moves personal files with `gio trash --`; empties only with `gio trash --empty`; has no direct-delete fallback. | `appssweep/trash_manager.py`, `appssweep/cleanup_engine.py` | Mocked Trash move, Trash empty, missing-`gio` report-only path, and Trash preview tests. | **Binary present; real Trash behavior unverified.** |
| Capability detection | Checks PATH visibility without executing host tools; reports available/missing tools, actionable limits, and the mock/unverified status. | `appssweep/capabilities.py` | `tests/test_capabilities.py` verifies availability, warnings, empty reports, and no command execution. | **Not applicable; detection itself is local and read-only.** |

## What is disabled when a tool is missing

| Missing tool | Still available | Disabled or report-only |
| --- | --- | --- |
| `dnf`/`dnf5` and `rpm-ostree` | Scan, personal-file report, caches, logs, Trash (with `gio`) | Package cache, autoremove, and old-kernel cleanup |
| `rpm` while DNF is available | DNF cache cleanup and autoremove preview | Old-kernel metadata and old-kernel removal |
| `flatpak` | Everything else | Flatpak runtime cleanup |
| `journalctl` | Scan and read-only journal-size measurement | Journal vacuum |
| `pkexec` | Non-admin cache and Trash actions | Any action that needs administrator approval |
| `gio` | Scan, report, caches, package/log cleanup | Moving personal files to Trash and emptying Trash; no permanent-delete fallback |
| `rpm-ostree` on a non-ostree system | DNF-based cleanup if DNF is present | Nothing extra; rpm-ostree is not applicable |

The `HOST INTEGRATIONS` section in the terminal shows the current result. JSON
reports include the same data under `capabilities`, with
`real_host_verified: false` in this release. It may become true only when a
release records completed checklist evidence for a real Fedora host; mock-only
changes must not set it.

## Automated mock coverage

These tests are explicitly mocked. They do not invoke the named host tools.

| Test file | Coverage |
| --- | --- |
| `tests/test_capabilities.py` | PATH-only detection, all-missing warnings, DNF5-only handling, RPM/DNF warning selection, empty-report behavior, and no command execution during detection. |
| `tests/test_host_integration_mocks.py` | Missing package manager/Flatpak fallback, rpm-ostree metadata discovery, RPM old-kernel discovery with running-kernel exclusion, journal usage parsing, journalctl vacuum discovery, and no-journalctl report-only fallback. |
| `tests/test_package_flatpak.py` | DNF/DNF5 detection, autoremove parsing and cacheonly preview, Flatpak runtime parsing and unavailable fallback, command shapes, and pkexec behavior. |
| `tests/test_cleanup_engine.py` | `gio trash --` for personal files, no-permanent-delete fallback when `gio` is missing, and allowlisted command validation. |
| `tests/test_trash.py` | Trash size/count/examples and the no-`gio` report-only path. |
| `tests/test_cli.py` | Report-only JSON includes capability status, the terminal shows `HOST INTEGRATIONS`, and no real-host claim is made. |

Run the mocked suite:

```bash
python3 -m compileall -q appssweep tests
python3 -m unittest discover -v
```

Current result: 166 tests total; 165 pass and 1 Rich-only test skips without
Rich; all 166 pass with Rich. These are mock/unit results, not host validation.

Optional Rich run:

```bash
/tmp/opencode/appssweep-rich-venv/bin/python -m unittest discover -v
```

## Manual real-Fedora validation checklist

Use a **disposable Fedora virtual machine or a test account with no important
data**. Do not install or reconfigure host packages merely to make an item pass.
Do not run AppSweep as root. For any cleanup step, use explicit, disposable test
data and the normal AppSweep `CLEAN` confirmation.

### 1. Record the test environment

- [ ] `cat /etc/fedora-release`
- [ ] `uname -r`
- [ ] `python3 --version` (include a Python 3.10 run if supported)
- [ ] `command -v dnf dnf5 rpm-ostree rpm flatpak journalctl pkexec gio`
- [ ] Record the AppSweep commit and `appssweep --version`.
- [ ] Record which tools are absent. Mark absent tool rows “N/A”, not “FAIL”.

### 2. Capability report and graceful fallback

- [ ] Run:
  ```bash
  appssweep --mode quick --report-only --no-color
  ```
- [ ] Confirm `HOST INTEGRATIONS` lists available and missing tools.
- [ ] Confirm every missing tool has an actionable limit and that the scan still
      completes with exit code 0.
- [ ] Export JSON and confirm `capabilities.real_host_verified` is `false` until
      this checklist is signed off:
  ```bash
  appssweep --mode quick --report-only --no-color --json /tmp/appssweep-check.json
  ```
- [ ] Confirm no host command was executed merely to produce the capability list.

### 3. DNF or DNF5

Use a disposable Fedora system or test user with permission to run the normal
package-manager checks.

- [ ] `dnf --version` or `dnf5 --version` succeeds.
- [ ] Run AppSweep with `--categories packages` in report-only mode.
- [ ] Confirm the DNF cache item appears only when a real cache directory has
      content.
- [ ] Confirm the autoremove preview uses the package manager's non-executing
      preview and does not remove packages in report-only mode.
- [ ] If `--refresh-metadata` is tested, confirm it is opt-in and that a failure
      is reported without changing the system.
- [ ] On a disposable system, and only with explicit authorization, run one
      confirmed cache cleanup. Confirm it used `dnf clean all`/the detected
      binary and did not delete package files directly.
- [ ] Confirm an old-kernel item, if offered, excludes the running kernel and a
      recovery kernel. Cross-check with:
  ```bash
  rpm -qa --qf '%{NAME}|%{VERSION}-%{RELEASE}|%{ARCH}\n' | sort
  uname -r
  ```
- [ ] Do not remove a kernel merely to complete this checklist unless the test
      system is disposable and recovery is verified.

### 4. rpm-ostree (immutable Fedora only)

- [ ] Run on an rpm-ostree/Silverblue-style test system.
- [ ] `rpm-ostree status` succeeds.
- [ ] Report-only discovery offers only metadata cleanup, not deployment or
      rollback removal.
- [ ] On a disposable system with explicit authorization, confirm the command is
      exactly `rpm-ostree cleanup -m`.
- [ ] Confirm installed applications, deployments, and rollback entries remain.

### 5. Flatpak

- [ ] `flatpak --version` succeeds.
- [ ] `flatpak list --app` and `flatpak list --runtime` show the test catalogs.
- [ ] Report-only discovery labels unused-runtime sizes as estimates.
- [ ] Install a disposable test runtime/fixture if already permitted; otherwise
      record N/A.
- [ ] On a disposable system with explicit authorization, confirm cleanup uses
      `flatpak --user uninstall --unused` or
      `flatpak --system uninstall --unused`, and that applications and app data
      are untouched.
- [ ] Confirm the actual reclaim can differ from the preview estimate.

### 6. journalctl

- [ ] `journalctl --disk-usage` succeeds.
- [ ] Confirm the parser handles the host's output format (for example `1.2G`,
      `1.2GB`, or `1.2GiB`) without reporting zero.
- [ ] Run report-only and confirm a journal item appears only when estimated
      reclaim exceeds the target threshold.
- [ ] On a disposable system with explicit authorization, vacuum to the offered
      target and confirm the command is `journalctl --vacuum-size=...`.
- [ ] Confirm AppSweep did not delete journal files directly.
- [ ] Temporarily hide `journalctl` from a test PATH and confirm the report-only
      fallback still measures journal directories without deleting them.

### 7. RPM old-kernel metadata

- [ ] Confirm `rpm -qa` returns kernel metadata without errors.
- [ ] Confirm AppSweep shows old kernels only when they are older than the
      protected running/recovery set.
- [ ] Confirm no `rpm -e` or direct kernel-file deletion is generated by
      AppSweep; DNF performs any authorized removal.
- [ ] Confirm an RPM query failure produces a note and does not crash discovery.

### 8. pkexec

- [ ] Confirm the application refuses to run with `sudo appssweep`/euid 0.
- [ ] Trigger one allowlisted system action and confirm pkexec asks for approval
      for that command only.
- [ ] Cancel the approval prompt; confirm the item is reported as failed and
      nothing was changed.
- [ ] Deny approval through the test policy; confirm the failure is reported
      without retrying or elevating the whole application.
- [ ] Confirm cache/personal actions that do not need admin still work without
      pkexec.

### 9. gio and Trash

- [ ] `gio --version` succeeds.
- [ ] Create a disposable test file in a temporary home or test directory.
- [ ] Use AppSweep to move it to Trash; confirm `gio trash --` is used and the
      file is recoverable from the desktop Trash.
- [ ] Empty the Trash on the disposable test account; confirm
      `gio trash --empty` is used.
- [ ] Test a file on a different local filesystem if available; record any
      cross-filesystem limitations.
- [ ] Remove/hide `gio` from the test PATH and confirm files become report-only;
      confirm no permanent deletion fallback occurs.

### 10. Full scan and coverage

- [ ] Run `appssweep --mode full --report-only --no-color` on a representative
      Fedora test system.
- [ ] Confirm `/home`, `/var`, `/usr`, `/boot`, `/opt`, `/etc`, `/root`, `/srv`,
      caches, logs, and Trash are represented where accessible.
- [ ] Confirm separately mounted local filesystems are included and virtual,
      network, and external mounts are skipped with reasons.
- [ ] Create a disposable same-device bind mount below a scan root and confirm
      it is pruned by default and reported as a boundary. Select it explicitly
      as a custom root and confirm it is scanned once, not twice. Remove the
      fixture mount afterward.
- [ ] Confirm there is no double counting between nested roots.
- [ ] Confirm permission failures are visible and do not pretend coverage is
      complete.
- [ ] Check runtime and memory use on a large home directory.

### 11. Cache and personal-deletion boundary test

Use a disposable test account and disposable fixtures only.

- [ ] Create a temporary cache directory with a file inside, plus a bind mount
      inside that directory pointing at a separate disposable directory outside
      the cache tree. Confirm the cache cleanup policy reports the mount
      boundary and leaves the outside fixture intact.
- [ ] Confirm the same check works for a same-device bind mount, not only a
      separate device.
- [ ] Create a disposable personal file and exercise the normal Trash path;
      confirm no permanent-delete fallback occurs.
- [ ] If permanent deletion is explicitly authorized on disposable data,
      confirm a changed parent or changed file is reported and not removed.
- [ ] Record that `/proc/self/fdinfo` mount-ID verification is available on the
      host; if it is not, confirm cache cleanup fails closed instead of deleting.

### 12. Python 3.10 compatibility

- [ ] Create a Python 3.10 environment on a supported Fedora release.
- [ ] Run:
  ```bash
  python3.10 -m compileall -q appssweep tests
  python3.10 -m unittest discover -v
  ```
- [ ] Run a quick report-only scan and a JSON/CSV export.
- [ ] Record any syntax, typing, or runtime incompatibility.

## Sign-off

| Field | Value |
| --- | --- |
| Fedora release | |
| Kernel | |
| AppSweep commit/version | |
| Python version | |
| Tools present (with versions) | |
| Tools absent / N/A | |
| Mocked suite result | |
| Real-host checklist result | |
| Tester and date | |
| Remaining follow-up | |

Only mark a release as fully host-validated when the relevant rows have real
evidence. A missing optional tool is a host limitation, not an AppSweep failure.
