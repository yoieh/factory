---
name: migrate
role: produce
kinds: [code]
agent: factory-worker
summary: Writes a schema, data, config or dependency migration with rollback steps and a dry run.
when: The job changes a database schema, moves or backfills data, renames or removes config keys that running systems read, or upgrades a dependency across a major version.
requires: [repo-tests]
gates_optional: false
skills_key: [code]
version: 1
---

## Focus
- Make the change with the repo's own migration tool and layout.
- Keep it reversible: every forward step has a backward step, or the output says why it cannot.
- Keep old and new code working during the rollout. Expand first (add, copy, read both), contract later (remove) in a separate change.
- Data migrations are idempotent, run in batches on large tables, and can stop and restart safely.
- Dependency upgrades: read the changelog between the two versions, fix every breaking change the repo hits, and update the lockfile with the repo's tool.
- Config changes: keep the old key working until every reader has moved, or name the deploy order.

## Read
- `### migrate` under `## Notes for steps` in `spec.md`.
- The repo's existing migrations, their naming, and how they run in tests.
- Every reader and writer of the data, config key or dependency you change, also in other project repos the spec names.
- The `step_skills.code` entries in `project` whose `when` fits this repo.

## Rules
1. Run the migration, the dry run and the rollback only against local or test databases and services you start yourself. Never against shared, staging or production systems.
2. Test both directions: forward, backward, then forward again. Run the full suite after.
3. Never drop data or a column in the same change that stops using it.
4. Name every lock, long-running step and downtime risk, with a size estimate when the spec gives data volumes.
5. Round 2 and later: fix every Must fix item from the previous round's gate files. A `rework.md` is a human request: do it.
6. No code comments; the reasons go in `output.md`. No factory words in code or commits.
7. Commit on `branch` in the repo's commit style. Stage files by name.

## Output
Beyond the standard `output.md`, add:
- `## Dry run`: the command, where it ran, and what it showed (the statements it would run, the rows it would touch, or the resolved dependency tree).
- `## Rollback`: numbered steps a person can follow, with the exact commands, and what data a rollback loses.
- `## Rollout`: the deploy order across systems, and any step to do by hand before or after.
- `## Risks`: locks, duration, downtime and data loss, or "none found".
