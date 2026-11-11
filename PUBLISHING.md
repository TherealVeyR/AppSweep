# Publishing AppSweep

This guide is the exact pre-publication, Git, GitHub, wheel, and release
procedure for AppSweep. It is intentionally conservative: a green test suite
does not prove Fedora host behavior, and a source repository can be publishable
before a stable release is justified.

Read `AGENTS.md`, `RELEASE_CHECKLIST.md`, `SECURITY.md`, and `INTEGRATIONS.md`
before starting. Do not commit, push, tag, or publish without explicit
permission.

## 0. Current state and release decision

This working tree contains:

- the `appssweep/` → `appsweep/` package, launcher, console-command, and audit
  state-directory migration;
- the final security remediation that disables permanent personal-file
  deletion;
- Trash-only personal-file behavior with no permanent-delete fallback;
- explicit report-only, dry-run, and `--refresh-metadata` messaging;
- the updated documentation, `SECURITY.md`, `PUBLISHING.md`, and MIT
  `LICENSE`.

Current verified result: **185 `unittest` tests, 184 pass, 1 Rich-only skip,
0 failures**. Compileall passes. A disposable-copy wheel build, archive
inspection, isolated install, console-script check, and report-only smoke scan
also pass. The exact evidence is in section 13; `build`/`twine` sdist checks
remain blocked.

Current release decision: **CONDITIONAL NO-GO for a fully validated Fedora
release.** You may publish the source repository for review after the preflight
and commit steps below. Do not publish a stable tag or claim “Fedora
validated” until the real-host checklist passes. `real_host_verified` must stay
`false`, permanent personal-file deletion stays disabled, and real Gio Trash
behavior remains blocked.

## 1. Decisions to make before publishing

Record these decisions in the release notes or an issue:

1. **Repository owner and name.** The package and command are `appsweep`; the
   user-facing product name is **AppSweep**. A repository named `appsweep` is
   consistent with the package and command.
2. **Visibility.** Public source and a public issue tracker are reasonable for
   a source review. Enable private security advisories first. If the repository
   stays private, the same checks still apply.
3. **License.** The included license is MIT. The publisher must confirm that
   they have the right to license all contributed code under MIT. Replace the
   generic copyright line only if the publisher has a specific legal name they
   want to use; otherwise keep “AppSweep contributors.”
4. **Version.** `pyproject.toml` is `1.0.0`. Do not tag `v1.0.0` as a stable
   release while the real-Fedora gates are open. A source-only publication can
   remain on the default branch without a tag.
5. **Release type.** If a release artifact is needed before full validation,
   use an explicit pre-release label such as `v1.0.0-rc.1` and state the
   limitations. Do not use “stable” or “Fedora validated.”
6. **Support channel.** Use GitHub Issues for normal bugs and a private
   security advisory for vulnerabilities. Do not ask researchers to post
   exploit details publicly.
7. **Host evidence.** Name the person and disposable Fedora environment that
   will complete `INTEGRATIONS.md`. Mocked tests cannot satisfy that checklist.

## 2. Understand the worktree migration

The intended Git change is a case-sensitive package rename:

```text
tracked before:  appssweep/  bin/appssweep
working tree:    appsweep/   bin/appsweep
```

Until the change is staged, `git status` shows the old paths as deleted and the
new paths as untracked. That is expected. Do not “fix” it by deleting either
tree manually. Stage the complete migration and inspect the rename detection.

`INDEPENDENT_VALIDATION_REPORT.md` is historical evidence for the older
baseline and is ignored for publication. `Archive.zip` is also ignored. Do not
add either to the release commit. Do not rewrite them to the new package name;
they are evidence of past states.

## 3. Preflight command set

Run every command from the repository root:

```bash
pwd
git rev-parse --show-toplevel
git status --short --untracked-files=all
git log --oneline --decorate -8
python3 --version
```

Confirm the output matches the expected current state. On this development
environment the expected Python is 3.14.9; a real Fedora release must also be
tested on Python 3.10.

### 3.1 Compile and test

```bash
python3 -m compileall -q appsweep tests
python3 -m unittest discover -v
```

Expected:

- `compileall` exits 0.
- 185 tests run.
- 184 pass.
- 1 Rich-only test skips because Rich is not installed.
- 0 failures and 0 errors.

Run the safety-focused suites separately so a later failure is easy to locate:

```bash
python3 -m unittest -v tests.test_safety
python3 -m unittest -v tests.test_cleanup_engine
python3 -m unittest -v tests.test_cli
python3 -m unittest -v tests.test_package_flatpak
```

Optional checks when the tools exist:

```bash
ruff check appsweep tests
mypy appsweep
pyright appsweep
pytest -q
```

Those tools are absent in the current environment. Record them as **BLOCKED**,
not passing. A skipped test is not a passing test.

### 3.2 Diff hygiene

```bash
git diff --check
find . -path ./.git -prune -o -type f -name '*.py' -print0 \
  | xargs -0 grep -nE 'TODO|FIXME|XXX' || true
```

`git diff --check` must exit 0. Remove trailing whitespace and extra blank
lines at end of file before committing.

### 3.3 Secret and personal-data scan

Use a case-insensitive scan and review every hit. False positives are expected
for words such as `token` in tests and `secret.bin` in fixtures.

```bash
grep -RIn --exclude-dir=.git \
  -E 'BEGIN (RSA|OPENSSH|EC|PGP) PRIVATE KEY|api[_-]?key|access[_-]?token|password[=:]|secret[=:]' . || true
grep -RIn --exclude-dir=.git \
  -E '/home/[A-Za-z0-9._-]+|<owner>|ghp_[A-Za-z0-9]+' . || true
```

Expected: no real credentials, no personal scan reports, no audit logs, and no
machine-specific absolute home paths. The project root in `AGENTS.md` is
relative by design.

### 3.4 Artifact and permission review

```bash
git status --short --untracked-files=all
git diff --stat
git diff --summary
find . -path ./.git -prune -o -type f -perm -002 -print
```

Do not publish virtual environments, `build/`, `dist/`, `*.egg-info`,
`__pycache__`, `.ruff_cache`, `.pytest_cache`, generated reports, audit logs,
`Archive.zip`, or `INDEPENDENT_VALIDATION_REPORT.md`. The `.gitignore` covers
those paths.

### 3.5 Documentation consistency

```bash
grep -RIn --exclude-dir=.git --exclude=INDEPENDENT_VALIDATION_REPORT.md \
  -E '176 tests|175 pass|DELETE PERMANENTLY|permanent deletion is a separate choice' . || true
```

The only acceptable `DELETE PERMANENTLY` references are explicit statements
that the prompt is no longer offered or that the historical code is disabled.
Update any stale test count to 185/184/1.

Check that these files exist and are current:

```text
README.md
ARCHITECTURE.md
INTEGRATIONS.md
RELEASE_CHECKLIST.md
SECURITY.md
PUBLISHING.md
LICENSE
CHANGELOG.md
PROGRESS.md
TODO.md
AGENTS.md
CONTRIBUTING.md
temp_task.md
```

## 4. Review and stage the commit

Do not commit a dirty tree blindly. First inspect the full diff and untracked
files:

```bash
git status --short --untracked-files=all
git diff -- appssweep appsweep tests pyproject.toml
```

Because the new package is untracked, `git diff` will not show its contents
until staging. Stage the complete intended migration, then inspect the staged
result:

```bash
git add -A
git status --short
git diff --cached --stat
git diff --cached --summary
git diff --cached --find-renames --summary
git diff --cached --check
```

Requirements:

- The staged tree contains `appsweep/` and `bin/appsweep`.
- It does not contain `appssweep/`, `bin/appssweep`, `Archive.zip`, or
  `INDEPENDENT_VALIDATION_REPORT.md`.
- The staged tree contains `LICENSE`, `SECURITY.md`, and `PUBLISHING.md`.
- Only intended files are staged.
- No generated report, audit log, cache, venv, or build artifact is staged.

If a generated file was staged by mistake, unstage it without deleting it from
the working tree:

```bash
git restore --staged <path>
```

If a path is machine-specific and should never be published, add it to
`.gitignore`, then unstage it. Do not use `git clean`.

## 5. Commit strategy

The rename, safety remediation, tests, and documentation must land together.
A split commit is acceptable only if every intermediate commit passes the test
suite and contains no stale permanent-deletion behavior. For this working tree,
one atomic commit is the simplest safe choice:

```bash
git diff --cached --check
git commit -m "fix: fail closed on permanent personal-file deletion"
```

Suggested commit subject alternatives:

```text
fix: disable unsafe permanent personal-file deletion
refactor!: rename package to appsweep and harden cleanup boundaries
```

Use `!` only if the project treats the package/command/state-path rename as a
breaking change. The user-facing product name did not change.

After the commit:

```bash
git status --short
git log --oneline --decorate -3
git show --stat --oneline --decorate HEAD
```

The working tree should be clean. The commit must include the rename and the
new publication files. Do not amend or rewrite a commit that has been shared.

## 6. Build and inspect the final wheel

Build from the committed tree, not from an untracked or dirty working copy, so
the artifact proves what is actually in Git. Use a disposable directory under
`/tmp/opencode`.

```bash
SRC=/tmp/opencode/appsweep-wheel-src
DIST=/tmp/opencode/appsweep-wheel-dist
VENV=/tmp/opencode/appsweep-wheel-venv
rm -rf "$SRC" "$DIST" "$VENV"
mkdir -p "$SRC" "$DIST"
git archive --format=tar HEAD | tar -xf - -C "$SRC"
python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/python" -m pip wheel --no-deps --no-build-isolation \
  --wheel-dir "$DIST" "$SRC"
ls -l "$DIST"
```

`--no-build-isolation` avoids a network dependency on build backends. It uses
the installed setuptools and wheel; `pyproject.toml` requires setuptools 77 or
newer for the PEP 639 license metadata. If it fails because the build backend is
missing, record the failure and do not claim a wheel was built. Do not install
dependencies from the network as part of a release check without explicit
authorization.

Inspect the archive with the standard library:

```bash
WHEEL=$(ls -1 "$DIST"/appsweep-*.whl | head -n 1)
python3 -m zipfile -l "$WHEEL"
```

Required checks:

- The wheel contains `appsweep/`, `appsweep-*.dist-info/`, `LICENSE`, and the
  console entry point.
- It contains **no** `appssweep/` path.
- `METADATA` declares `Name: appsweep`, `Version: 1.0.0`,
  `Requires-Python: >=3.10`, and the MIT license expression.
- `entry_points.txt` contains `appsweep = appsweep.cli:main`.
- The wheel does not contain tests, reports, audit logs, caches, or the
  historical validation report.

A compact inspection script:

```bash
"$VENV/bin/python" - "$WHEEL" <<'PY'
import sys, zipfile
wheel = sys.argv[1]
with zipfile.ZipFile(wheel) as zf:
    names = zf.namelist()
    assert any(n.startswith("appsweep/") for n in names)
    assert not any(n.startswith("appssweep/") for n in names)
    assert any(n.endswith("/LICENSE") or n == "LICENSE" for n in names)
    assert not any(n.startswith("tests/") for n in names)
    meta = next(n for n in names if n.endswith(".dist-info/METADATA"))
    text = zf.read(meta).decode("utf-8")
    print(text)
    eps = next(n for n in names if n.endswith(".dist-info/entry_points.txt"))
    print(zf.read(eps).decode("utf-8"))
PY
```

Install the wheel into a clean disposable environment and verify the command
from outside the source tree:

```bash
INSTALL=/tmp/opencode/appsweep-wheel-install
rm -rf "$INSTALL"
python3 -m venv --system-site-packages "$INSTALL"
"$INSTALL/bin/python" -m pip install --no-deps --force-reinstall "$WHEEL"
cd /tmp
"$INSTALL/bin/appsweep" --version
"$INSTALL/bin/python" - <<'PY'
import appsweep
print(appsweep.__file__)
PY
```

Expected: `appsweep 1.0.0`, and the imported path is under the disposable
virtual environment, not the source checkout.

Recorded result for this working tree: the wheel built as
`appsweep-1.0.0-py3-none-any.whl` (97,932 bytes, SHA-256
`7cd2c69905d67ae5dccf0452a70c190adeb1beddfcab7bcda001a218d63549ab`) with 30
entries. The archive contained only `appsweep/` and
`appsweep-1.0.0.dist-info/`, plus the MIT license and the console entry point;
it contained no `appssweep/` or tests. A disposable venv installed it,
`appsweep --version` printed `appsweep 1.0.0`, and a report-only custom scan
using a synthetic `keep.txt` exited 0, wrote JSON, left the fixture unchanged,
and created no audit log. `pip uninstall -y appsweep` removed both the console
script and the importable package.

If `python -m build` is later installed, also run:

```bash
python3 -m build
python3 -m twine check dist/*
```

Those are **BLOCKED** in the current environment because `build` and `twine`
are not installed. A successful `pip wheel` is not an sdist check.

## 7. Create or connect the GitHub repository

Inspect the existing remote before changing it:

```bash
git remote -v
git branch --show-current
git log --oneline --decorate -5
```

If there is no remote and you have permission to publish:

```bash
git remote add origin git@github.com:<owner>/appsweep.git
git branch -M main
git push -u origin main
```

If a remote already exists, verify its URL and fetch before pushing:

```bash
git remote -v
git fetch origin
git status --short
git log --oneline --decorate --graph --all -10
```

Then push normally:

```bash
git push -u origin main
```

Never use `git push --force`, `--force-with-lease`, or a history rewrite to
“fix” a rejected push. Stop and reconcile the remote first.

If the repository is new, the web UI can create an empty repository without a
README, license, or `.gitignore` so the first push is not rejected. If the UI
adds a commit, merge it normally; do not force.

## 8. Configure the repository

After the first push:

- Set the default branch to `main`.
- Add a short description: “Safety-first Fedora storage analyzer and cleanup
  assistant.”
- Add topics such as `fedora`, `storage`, `disk-space`, `cleanup`, `python`,
  and `cli`.
- Confirm GitHub detects the MIT license.
- Enable **Private vulnerability reporting** under Settings → Security.
- Ensure `SECURITY.md` is linked from the repository’s security policy.
- Enable Issues for normal bugs and Discussions only if you will moderate them.
- Require the test workflow for pull requests if branch protection is enabled.
- Do not expose secrets, personal scan data, or audit logs in issues.

A minimal CI workflow is included at `.github/workflows/tests.yml`. It runs
compileall and the standard-library test suite on supported Python versions. It
does not run real Fedora tools, Gio, pkexec, Ruff, or type checkers. Those remain
manual gates.

## 9. Release and tag policy

Do **not** create a stable release tag from the current tree. The release
decision in `RELEASE_CHECKLIST.md` is conditional no-go because real Fedora
host and Gio validation are outstanding.

When the real-host checklist is complete and the publisher approves:

```bash
git status --short
git log --oneline --decorate -3
git tag -a v1.0.0 -m "AppSweep 1.0.0"
git push origin v1.0.0
```

If signed tags are configured, use `git tag -s` and verify the signature. Then
create the GitHub release from the tag, attach the inspected wheel and, when
available, the sdist, and include:

- the exact test results and environment;
- the real-Fedora checklist results;
- the known limitations from `README.md` and `SECURITY.md`;
- the fact that permanent personal-file deletion is disabled;
- the fact that Gio Trash retains an accepted same-UID pathname race;
- the real-host verification status;
- a link to `SECURITY.md` for vulnerability reporting.

For a pre-release:

```bash
git tag -a v1.0.0-rc.1 -m "AppSweep 1.0.0 release candidate 1"
git push origin v1.0.0-rc.1
```

Mark the GitHub release as a pre-release and repeat the limitations. Do not
call it stable.

## 10. Package registry publication (optional, later)

Do not publish to PyPI before the stable release decision. When ready:

```bash
python3 -m build
python3 -m twine check dist/*
python3 -m twine upload --repository testpypi dist/*
# Verify the TestPyPI install in a clean environment.
python3 -m twine upload dist/*
```

Use a scoped PyPI API token stored in `~/.pypirc` or supplied by Twine’s
prompt. Never put a token in the repository, shell history, or a workflow file.
Verify:

```bash
pipx install appsweep==1.0.0
appsweep --version
```

If a bad release is published, prefer `twine yank` or a GitHub release
withdrawal over deleting history. Publish a fixed version and explain the
problem.

## 11. Post-publication verification

From a clean machine or disposable environment:

```bash
git clone <repository-url> appsweep-review
cd appsweep-review
python3 -m compileall -q appsweep tests
python3 -m unittest discover -v
./bin/appsweep --version
```

Check:

- The clone contains `appsweep/`, not `appssweep/`.
- The suite runs without network access and without Rich.
- The console script works after `pip install .` in a venv.
- The README commands match the actual command and package names.
- `LICENSE`, `SECURITY.md`, and `PUBLISHING.md` are present.
- `git status` is clean.
- No generated report, audit log, or cache is present.

## 12. Troubleshooting

### Git shows the old package as deleted and the new package as untracked

That is the case-sensitive rename. Review and stage both sides:

```bash
git add -A
git diff --cached --find-renames --summary
```

Do not delete the new package to make the status look clean.

### The wheel contains `appssweep/`

The build came from a stale copied tree or the package list in
`pyproject.toml` is wrong. Re-extract from `HEAD`, confirm
`[tool.setuptools] packages = ["appsweep"]`, rebuild, and inspect again.

### `pip wheel` tries to reach the network

Use `--no-build-isolation` in the disposable environment that already has
setuptools and wheel. If the backend is missing, record the check as blocked;
do not silently install from the network during a release audit.

### `git diff --check` reports “new blank line at EOF”

Remove the extra blank line at the end of the named file, re-run
`git diff --check`, and stage the fix.

### A push is rejected

Fetch and inspect. Do not force-push. Reconcile the upstream commit normally or
ask the repository owner before doing anything else.

### Security report arrives publicly

Ask the reporter to move details to a private advisory, remove sensitive
details if you can, and follow `SECURITY.md`. Do not discuss exploit details in
a public thread.

## 13. Exact evidence from this working tree

Recorded on 2026-11-11 in the development sandbox:

```text
python3 --version                  Python 3.14.9
python3 -m compileall -q appsweep tests   PASS
python3 -m unittest discover -v    185 tests, 184 pass, 1 Rich-only skip, 0 failures
git diff --check                   PASS after removing the extra README blank line
pytest                             NOT INSTALLED / not run
ruff                               NOT INSTALLED / blocked
mypy                               NOT INSTALLED / blocked
pyright                            NOT INSTALLED / blocked
build                              NOT INSTALLED / blocked
Rich-enabled current suite         BLOCKED; temporary venv unavailable
Real Fedora tools                  BLOCKED; DNF/DNF5/RPM/rpm-ostree/Flatpak/pkexec absent
Real Gio Trash                     BLOCKED; binary present, no real desktop Trash test
Python 3.10 runtime                BLOCKED; only 3.14.9 available
Python 3.10 syntax parse          PASS; not a runtime test
Real bind mount                    BLOCKED; unit fixtures and simulated mount IDs only
Clean final-wheel install          PASS; isolated install, console script, report-only smoke, and uninstall verified
sdist/twine check                  BLOCKED; build and twine are not installed
```

The previous 175-test Rich-enabled suite passed. That is historical evidence,
not a current 185-test Rich result.

## 14. What must not be claimed

Do not publish any of these claims without new evidence:

- “Fedora validated” or “real-host verified.”
- “Race-free Trash movement” or “same-UID safe.”
- “Permanent deletion is safe” or “inode-identity guaranteed.”
- “Python 3.10 supported” based only on a syntax parse.
- “Ruff/mypy/pyright clean” when those tools were not run.
- “Wheel install verified” before section 6 succeeds.
- “Stable release” while the checklist is conditional no-go.

The truthful summary is: AppSweep is a safety-first development snapshot with
a passing 185-test suite, a disabled permanent-deletion path, explicit
Trash-only behavior, and documented host-validation gaps. The source repository
can be published after the preflight and commit steps; a stable Fedora release
must wait for the real-host gates.
