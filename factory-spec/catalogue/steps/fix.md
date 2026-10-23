---
name: fix
role: produce
kinds: [code]
agent: factory-worker
summary: Makes the reproducing test pass with the smallest change and runs the full suite.
when: The job follows a reproduce group and must make its failing test pass. Use build when there is no reproducing test.
requires: [repo-tests]
gates_optional: false
skills_key: [code]
version: 1
---

## Focus
- Find the root cause of the failure the reproducing test shows.
- Fix it with the smallest change, at the layer where the fault is.
- Make the reproducing test pass and keep every other test green.
- When the same fault can hit nearby cases inside the spec's scope, cover each with a test.

## Read
- `### fix` under `## Notes for steps` in `spec.md`.
- The `output.md` of the `reproduce` group in `previous`: the test, its command and the failure.
- The code path from the test to the fault, and its git history for why it is the way it is.
- The `step_skills.code` entries in `project` whose `when` fits this repo.

## Rules
1. Do not change, weaken, skip or delete the reproducing test. If the test itself is wrong, ask a question before you change it.
2. No refactors, renames or cleanups beyond what the fix needs.
3. Run the reproducing test first and see it pass. Then run the full suite.
4. When `previous` holds no `reproduce` group, write the failing test first, see it fail, then fix the code. Say so in `output.md`.
5. Round 2 and later: fix every Must fix item from the previous round's gate files.
6. Add no code comments. Commit on `branch` in the repo's commit style. Stage files by name. No factory words in code or commits.

## Output
Beyond the standard `output.md`, add `## Fix`:
- Root cause: one to three sentences, with `path/file:line`.
- Reproducing test: its name and command, failing before the fix and passing after.
- Why this is the smallest change, and what else you could have changed but did not.

`Result` shows the full suite, for example "213 passed".
