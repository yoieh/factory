---
name: review-findings
role: gate
kinds: [files]
agent: factory-reviewer
summary: Checks that every claim has evidence and every question is answered or named as open.
when: The producer wrote findings, an analysis or a recommendation under out/. It is the default gate of research.
requires: []
version: 1
---

## Focus
- Every question in the spec's `## Scope` is answered, or named under Open points with what would settle it.
- Every claim has evidence a reader can follow.
- The evidence supports the claim. Open cited files at the cited commit and line, and rerun cheap commands that change nothing.
- The grade fits the evidence. `verified` needs first-hand evidence. A doc or the issue alone is `documented`.
- Facts, interpretation and recommendations stay apart.
- A contested point shows each side with its evidence.

## Must fix
- A claim with no evidence, or with evidence that does not support it.
- A grade that overstates how certain a claim is.
- A spec question that is neither answered nor named as open.
- A recommendation presented as a fact.

Wording, order and extra depth are notes.

## Read
- `### review-findings` under `## Notes for steps` in `spec.md`.
- `out/findings.md` and every other file under `out_dir`.
- The sources the findings cite: files in the worktrees listed in `sources`, docs, the issue.

## Rules
1. Do not edit anything under `out_dir` or in any repo.
2. Read code only through `sources`, without changing it. Never check out, switch branches or stash; use `git -C <worktree> show <sha>:<file>` for a cited commit.
3. Check at least three claims, and every `verified` claim an answer rests on.
4. Under Notes, list each claim you checked and what you found.
5. Do not raise a point again that a human or an earlier round settled.
