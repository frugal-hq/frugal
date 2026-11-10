# Frugal: change

- Refactor: run the check before and after; same behavior; no feature change in the same diff.
- Migration (schema, data, API, dependency): old and new readers work until the switch; state the rollback; copy data, never move it in one step.
- Verify-only task: prove each acceptance condition with the smallest check; no edits.
- Commit message: Conventional Commits, one line, why over what; follow the repo's existing style first.
