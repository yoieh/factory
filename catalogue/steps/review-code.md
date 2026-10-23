---
name: review-code
role: gate
kinds: [code]
agent: factory-reviewer
summary: Checks correctness, tests, scope and the repo's conventions.
when: Every code group. It is required there unless the project turns code review off.
requires: []
skills_key: [code]
version: 1
---

## Focus
- Correctness: logic, edge cases, error handling, null and empty input.
- Every acceptance criterion in `spec.md` is met and proven by a test.
- Tests are meaningful, not just present. Run the test command from `output.md` and confirm its result.
- The repo's conventions and rules: structure, naming, lint, formatting, migrations.
- No code comments, dead code, debug output or changes outside the job's scope.
- Nothing about the factory in code, files or commit messages.

## Must fix
- A bug.
- An unmet acceptance criterion, or one without a test that proves it.
- A failing test, or a result in `output.md` that you cannot reproduce.
- A broken repo rule.
- A security hole you happen to see.

Style, naming taste and refactor ideas are notes. If in doubt, it is a note.

## Read
- `### review-code` under `## Notes for steps` in `spec.md`.
- The group diff: `git -C <worktree> diff <group_base>..<branch>`, and its log: `git -C <worktree> log --oneline <group_base>..<branch>`. The full job diff, `git -C <worktree> diff <feature_branch>...<branch>`, is context only.
- The repo's lint and format config, and how its tests are laid out.
- The `step_skills.code` files whose `when` fits this repo. When none fits, review against the repo's own docs and say so in one line under Notes.
- For each `upstream` job: the Notes of its last gate round. Check that every note that names this job or this repo was handled.

## Rules
1. Do not edit code, commit or push. Read-only commands and tests in the worktree are fine.
2. In a `reproduce` group the new test is meant to fail. Check that it fails for the bug's reason, asserts the correct behaviour, and that nothing else fails.
3. In a `fix` group, check that the reproducing test was not changed, weakened or skipped.
4. Do not raise a point again that a human or an earlier round settled.
5. Each Must fix item names `path/file:line`, the problem, why it must be fixed, and the fix.
6. When `gates` includes `verify`, do not run the full build or the full test suite: verify runs them in the same worktree at the same time. Run only the targeted tests that the spec names (or the tests the diff adds), and leave the full-suite claim in `output.md` to verify, which must pass too. Say under Notes which tests you ran.
