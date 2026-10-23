---
name: verify
role: gate
kinds: [code]
agent: factory-reviewer
summary: Runs the app or the tests end to end and checks each acceptance criterion by behaviour, not by reading.
when: The acceptance criteria describe behaviour someone can check by running the app, an endpoint or a command. It is the second gate of a bug fix.
requires: [repo-tests]
version: 1
---

## Focus
- Check every acceptance criterion in `spec.md` by running something, not by reading code.
- Prefer the most direct check: call the endpoint, run the command, drive the screen, run the end-to-end tests.
- Bug fix: follow the steps of the bug report and confirm the bug is gone, then run the reproducing test.
- Watch for side effects near the change: errors in the logs, a neighbouring flow that broke.

## Must fix
- A criterion whose behaviour does not match the spec.
- The app or the tests fail to start because of the change.
- An error or a crash on a path the spec covers.

A criterion you could not check because of the environment, such as a missing external service, is a note, not must-fix.

## Read
- `### verify` under `## Notes for steps` in `spec.md`: how to run and what to check.
- The repo facts in `plan.md`: the test command, the Worktree setup, and how to start the app.
- The repo's README and scripts for running the app locally and its end-to-end tests.
- The producer's `output.md`, for which criteria it claims and how.

## Rules
1. Do not edit code, commit or push. When you finish, `git -C <worktree> status` shows no changes.
2. Run only against local or test services you start yourself. Never touch shared, staging or production systems or data.
3. Use free local ports, and stop every process you started before you write the verdict.
4. Never print or save secrets from config or the environment.
5. Under Notes, list every criterion as `- [x] <criterion>: <what you ran>, <what you saw>` or `- [ ] <criterion>: not checked, <why>`.
6. A criterion left unchecked because of the environment goes under Notes with the reason, so it reaches the PR's Open points.
7. When you could check nothing at all, ask a question instead of passing.
