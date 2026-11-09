# Current Task Cache

Last updated: 2026-11-09

## Original next steps before this steering directive

I was finishing the AppSweep release-candidate review:

1. Re-run compileall, the full unittest suite, and the Rich suite after the
   latest safety changes.
2. Run a final Ruff pass and only fix real issues.
3. Re-run QUICK/CUSTOM/FULL smoke scans and JSON/CSV export checks.
4. Re-run `pip install .` and verify the installed console script.
5. Confirm `/home/Echo/Documents/Default Project/taskvault` remains untouched.
6. Update `PROGRESS.md`, `TODO.md`, `AGENTS.md`, and this file with the final
   verified state.
7. Continue the release review without starting unrelated features.

At that point the verified baseline was 107 tests (106 pass + 1 Rich-only skip
without Rich; 107/107 with Rich), Ruff clean, and no known code bugs in tested
paths. Real Fedora host-tool validation was still blocked by the Flatpak
sandbox.

## New active task from the steering directive

Bring AppSweep to a high release-quality standard without discarding working
code:

1. Inspect the current project, Git state, modules, tests, and documentation.
2. Initialize Git safely inside the AppSweep project if no repository exists.
3. Correct any `AppsSweep` branding to the official `AppSweep` spelling.
4. Expand persistent project memory with `CHANGELOG.md`, `CONTRIBUTING.md`, and
   updated `AGENTS.md` Git/testing/safety rules.
5. Identify and implement remaining release gaps, prioritizing:
   - full-filesystem scanner reliability;
   - exact top-50 reporting;
   - numbered interactive selection;
   - safe cleanup discovery and execution;
   - audit logging, dry-run support, and race-condition protection;
   - tests, packaging, and documentation.
6. Run the full verification suite, review the diff, and create verified
   conventional commits at meaningful milestones.
7. Never touch `../taskvault`, never use a shell in the product, never run the
   app as root, and never execute destructive operations against real user
   files during development or testing.

## Safety reminders

- The workspace contains real user files. Inspect before deleting anything.
- Test only inside temporary directories.
- Existing cleanup paths must remain explicitly confirmed and allowlisted.
- Do not publish, push, or create a GitHub release without permission.
