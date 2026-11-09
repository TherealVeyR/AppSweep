# Contributing to AppSweep

AppSweep is a safety-first storage analyzer and cleanup assistant for Fedora.
It handles real personal files and system cleanup, so a change that makes the
application more convenient at the expense of safety is not acceptable.

Read `AGENTS.md`, `PROGRESS.md`, `TODO.md`, `ARCHITECTURE.md`, and
`INTEGRATIONS.md` before making substantial changes.

## Safety rules

- Scanning and discovery are read-only.
- Cleanup must remain report-only by default.
- A destructive plan only runs after the user types `CLEAN`.
- Personal files default to Trash. Permanent deletion needs the separate
  `DELETE PERMANENTLY` phrase and the `permanent_personal` authorization bit.
- Never use `shell=True`, `os.system`, `eval`, or shell-concatenated commands.
- Never run the whole application as root. Use `pkexec` only for a specific
  allowlisted command that needs administrator approval.
- Never follow symlinks during scanning or cleanup. Use
  `O_DIRECTORY | O_NOFOLLOW`, `dir_fd` operations, and descriptor identity
  checks for destructive filesystem operations.
- Revalidate every personal-file snapshot immediately before acting. Permanent
  unlink must validate the parent chain and complete snapshot through the parent
  descriptor used for `unlink(..., dir_fd=...)`.
- Do not broaden cache cleanup into home configuration, SSH/GPG data, Trash
  internals, or system trees. Cache traversal must reject different devices and
  mount IDs, including same-device bind mounts, and fail closed when boundary
  verification is unavailable.
- Tests must use temporary directories. They must never delete or move real
  user data or invoke destructive host tools. A real bind-mount test requires
  explicit authorization and a disposable fixture; unit simulations are not
  real-mount evidence.
- Do not modify, test, format, or delete the unrelated `../taskvault` project.

If you are uncertain whether a cleanup action is safe, leave it report-only.

## Development setup

```bash
cd appssweep
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[rich]"       # optional Rich interface
./bin/appssweep --mode quick --report-only --no-color
```

The runtime has no required third-party dependencies. Rich is optional.

## Required checks

Run from the project root before committing:

```bash
python3 -m compileall -q appssweep tests
python3 -m unittest discover -v
```

Current result: 166 tests total; 165 pass and 1 Rich-only test skips without
Rich; all 166 pass with Rich. Run race tests as an unprivileged user; the
permission-dependent cases skip under root.

Optional lint and Rich checks:

```bash
ruff check appssweep tests
/tmp/appssweep-rich-venv/bin/python -m unittest discover
```

Host commands are mocked when the development sandbox lacks them. Do not
describe a mock as real Fedora validation.

## Code conventions

- Support Python 3.10+ and Fedora.
- Keep the standard library as the only required dependency.
- Preserve the ANSI interface when Rich is absent or fails.
- Keep filesystem-derived text out of executable command construction.
- Route display strings through `safe_terminal_text` and CSV cells through
  `csv_safe_cell`.
- Keep the personal-file report separate from cleanup items and preserve the
  exact displayed numbering.
- Prefer small modules and focused functions over adding responsibilities to
  `cli.py`.
- Add a regression test with every bug fix. Race and boundary fixes need both
  a victim-outside-the-target assertion and proof that authorized cleanup still
  works.
- Update user-facing documentation when behavior changes.

### Adding a cleanup action

A new cleanup action must have all of the following before it is complete:

1. A discovery path that explains the candidate and its size.
2. An `ActionKind` allowlisted implementation.
3. Exact command-shape validation for any external command.
4. Authorization coverage and a protected-path review.
5. A test using temporary files or a mocked tool.
6. Documentation in `README.md`, `ARCHITECTURE.md`, and `CHANGELOG.md`.

Do not add arbitrary paths or commands from user input.

## Git workflow

The Git repository root is the `appssweep/` project directory. Do not initialize
a nested repository in the parent workspace or in `../taskvault`.

Use conventional commits at meaningful, verified milestones:

```text
feat: add scanner cancellation coverage
fix: prevent symlink traversal in cache cleanup
test: cover audit-log refusal behavior
docs: add Fedora troubleshooting guide
refactor: separate cleanup validation from execution
```

Before each commit:

1. Inspect the diff.
2. Run the relevant tests.
3. Check for secrets, personal paths, scan reports, and generated artifacts.
4. Stage only intended files.
5. Commit only verified work.

Never commit virtual environments, build output, test caches, audit logs, scan
results, credentials, or machine-specific configuration. Never rewrite existing
history. Do not push or publish anything without explicit permission.

## Documentation

Update the appropriate files when a milestone changes behavior:

- `README.md` for user-facing installation, usage, safety, and troubleshooting.
- `PROGRESS.md` for verified completed work and evidence.
- `TODO.md` for remaining work, risks, and host-validation items.
- `ARCHITECTURE.md` for module boundaries, data flow, and security decisions.
- `INTEGRATIONS.md` for host-tool implementation status, mocked coverage, and
  real-Fedora validation results.
- `CHANGELOG.md` for notable changes.
- `RELEASE_CHECKLIST.md` before declaring a release candidate complete.
- `temp_task.md` for the active session's current task and next command.

Do not claim functionality that has only been mocked or planned.

## Release process

Complete `RELEASE_CHECKLIST.md` and obtain explicit permission before creating a
GitHub release, publishing a package, or pushing a release tag. A release is not
complete until it has been validated on a real Fedora host.
