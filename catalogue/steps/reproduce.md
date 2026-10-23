---
name: reproduce
role: produce
kinds: [code]
agent: factory-worker
summary: Writes a failing test that shows the bug and commits it, with no fix.
when: The job fixes a bug whose behaviour a test can show. Use it as the first group of a bug fix, before fix.
requires: [repo-tests]
gates_optional: false
skills_key: [code]
version: 1
---

## Focus
- Find the shortest path that triggers the bug the spec and the issue describe.
- Write one test, or a few, that fails today because of the bug and will pass once it is fixed.
- Assert the correct behaviour, not the current wrong one.
- Put the test where the repo keeps tests for that code, in the repo's test style.

## Read
- `### reproduce` under `## Notes for steps` in `spec.md`.
- The issue's description and comments: the steps, inputs, error texts and logs it quotes.
- The code path the bug goes through, and its existing tests.
- The `step_skills.code` entries in `project` whose `when` fits this repo.

## Rules
1. Change no production code. Test helpers and fixtures are fine when the test needs them.
2. The test must fail for the bug's reason. A failure from an import error, a missing fixture or a broken setup does not count.
3. Run the new test on its own and confirm the failure. Then run the full suite and confirm that only the new tests fail.
4. Do not write the fix, not even as a throwaway you revert: the fix group proves that the tests can turn green. To find existing tests that the fix will break, grep the tests for the names and values the bug touches. Correct a test that asserts the buggy behaviour, or list it for fix under Open points.
5. Do not mark the test as skipped or as an expected failure. The `fix` step must turn it green.
6. When you cannot reproduce the bug after a fair try, do not commit a passing test. Ask a question that says what you tried.
7. Round 2 and later: fix every Must fix item from the previous round's gate files.
8. Commit the test on `branch` in the repo's commit style. Stage files by name. No code comments and no factory words.

## Output
Beyond the standard `output.md`, add `## Reproduction`:
- Test: the test's name and file.
- Command: the command that runs only this test.
- Failure: the key lines of the failing output.
- Cause: one or two sentences on why the failure matches the bug, and where you think the fault is.

`Result` names the failing tests, for example "1 failed (new), 212 passed".
