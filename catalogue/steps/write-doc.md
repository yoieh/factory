---
name: write-doc
role: produce
kinds: [files, code]
agent: factory-worker
summary: Writes or updates documentation for a named audience, checked against the code.
when: The job writes or updates documentation such as a README, a guide, a reference page or a design note. Use a code job for docs in a repo and a files job for a document outside the repos.
requires: []
gates_optional: false
code_review: optional
skills_key: [docs]
version: 1
---

## Focus
- Write for the audience the spec names. Without one, write for the people who use or run the code.
- Start with what the reader wants to do. Then the reference detail.
- Every command, path, option, config key and example matches the code.
- Update every place the change touches: the README, reference pages, examples, the changelog when the repo keeps one.
- Plain language: short sentences, active voice, one term per idea, used the same way throughout.

## Read
- `### write-doc` under `## Notes for steps` in `spec.md`.
- The code the doc describes, at the branch the job builds on.
- The repo's existing docs, for structure, tone and file layout.
- For each `upstream` job: its `output.md`, for what landed and the contract as built.
- Round 2 and later: every gate file of the previous round.

## Rules
1. Files job: write only under `out_dir`, and read code only through the worktrees in `sources`, without changing them. Never check out, switch branches or commit.
2. Code job: work only inside `worktree` and change only documentation, unless the spec says otherwise. Commit on `branch` in the repo's commit style. Stage files by name.
3. Run every documented command that is safe to run locally, and copy its real output. Never run a command against shared or production systems.
4. Link to existing docs instead of copying them.
5. No secrets, real credentials or personal data in examples. No factory words.
6. Round 2 and later: fix every Must fix item from the gate files. A `rework.md` is a human request: do it.
7. Code job whose diff changes only documentation files: run `grep -rlF <path> <test dirs>` for each changed path (and its basename) and copy the output under Checked. When nothing matches and the repo has no docs build or link check, do not run the full suite; set Test command to that grep. When a test matches, run only those test files.

## Output
Beyond the standard `output.md`:
- `Test command`: the repo's docs build or link check when it has one, else `none`.
- Under `## Changes`, each doc with its audience in a few words.
- `## Checked`: every command and example you ran, with its result, and the ones you could not run and why.
