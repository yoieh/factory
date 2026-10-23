---
name: review-docs
role: gate
kinds: [code, files]
agent: factory-reviewer
summary: Checks docs for accuracy against the code, completeness and fit for the audience.
when: The group's producer wrote or changed documentation. It is the default gate of write-doc.
requires: []
skills_key: [docs]
version: 1
---

## Focus
- Accuracy: every command, path, option, config key, API and example matches the code.
- Completeness: the docs cover what the spec's scope asks, and every place the change touches is updated.
- Audience: written for the reader the spec names, starting with what that reader wants to do.
- Plain language: short sentences, active voice, terms defined once and used the same way.
- Links and references resolve.
- No secrets, credentials or personal data in examples. No factory words.

## Must fix
- A wrong statement: a command, path, option or behaviour that does not match the code.
- A part the spec asks for that is missing.
- A broken link, or an example that fails when run.
- A secret, a real credential or personal data.

Tone, order and wording are notes.

## Read
- `### review-docs` under `## Notes for steps` in `spec.md`.
- Code job: the group diff, `git -C <worktree> diff <group_base>..<branch>`, with the full job diff, `git -C <worktree> diff <feature_branch>...<branch>`, for context only. Files job: every file under `out_dir`.
- The code the docs describe.
- The repo's existing docs, for style and layout.
- For each `upstream` job: its `output.md`, for the contract as built.
- The `step_skills.docs` files in `project`, when listed.

## Rules
1. Do not edit the docs, the code or anything under `out_dir`. Do not commit or push.
2. Run documented commands that are safe to run locally. Never against shared or production systems. Name under Notes the ones you ran.
3. Files job: read code only through the worktrees in `sources`, without changing them. Never check out, switch branches or commit.
4. Each Must fix item names the doc and line, what is wrong, and what the code actually does.
5. Do not raise a point again that a human or an earlier round settled.
