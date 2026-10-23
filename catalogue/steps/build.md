---
name: build
role: produce
kinds: [code]
agent: factory-worker
summary: Implements the spec in the job worktree, adds tests and commits.
when: The job adds or changes behaviour in one repo. This is the default producer of a code job.
requires: []
gates_optional: false
skills_key: [code]
version: 1
---

## Focus
- Build what `spec.md` asks for and nothing more. The spec decides; the issue is background.
- Meet every acceptance criterion and prove each one with a test.
- Follow the repo's conventions, structure, naming and patterns. Reuse what the repo already has.
- When the spec has a `## Design` section, match each listed frame: layout, texts, states and spacing. Build it with the repo's own components, tokens and styles. Reference code in a frame's `.md` is a suggestion from the design adapter, not code to paste.
- When the spec has a `## Contract` section, build that interface exactly.

## Read
- `### build` under `## Notes for steps` in `spec.md`.
- Each frame `.png` and `.md` listed under `## Design`, and `design/notes.md` when it exists.
- For each `upstream` job: its `output.md` (the contract as built, Open points) and the Notes of its last gate round. Handle every note that names this job or this repo.
- Round 2 and later: every gate file of the previous round, and `rework.md` of this round when it exists.

## Rules
1. Work only inside `worktree`. Start shell commands with `cd <worktree> &&`.
2. When `setup` is not `ok`, run the repo's Worktree setup from `plan.md` first and fix what failed. Never symlink or reuse the main checkout's environment. If it still fails, write `STATUS: BLOCKED` with the command and the error.
3. Round 2 and later: fix every Must fix item. Notes do not block; take the cheap and clearly right ones. A `rework.md` is a human request: do it.
4. Run the tests for your change first, then the full suite when it runs in a few minutes.
5. Add no code comments and no debug output. Change nothing outside the job's scope.
6. A value the issue leaves open becomes a config placeholder, named under Open points, not a question.
7. Commit on `branch` in the repo's commit style. Stage files by name. Never mention the factory, a run or an agent in code, files or commit messages.

## Output
Beyond the standard `output.md`:
- Under `## Acceptance criteria`, name the test that proves each criterion.
- Under `## Changes`, list new config keys, environment variables, migrations and public interfaces, each with its default.
- When the spec has a `## Contract` section, add `## Contract as built`: the interface exactly as it landed, so dependant jobs can rely on it. Any difference from the spec's contract also goes under Open points.
