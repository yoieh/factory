---
name: review-ux
role: gate
kinds: [code]
agent: factory-reviewer
summary: Checks flows, states, copy and accessibility, against the design when there is one.
when: The job changes what a user sees or does, such as a screen, a flow, a form, copy or an interaction.
requires: []
skills_key: [ux]
version: 1
---

## Focus
- With a design: when the spec has a `## Design` section, check the flow against each listed frame (`.png` and `.md`) and `design/notes.md` when it exists. Every state drawn exists and behaves as drawn, and the texts match.
- Without a design: judge the flow against the spec, the issue and the screens around the change.
- The flow the spec describes works end to end, with no dead ends and a clear way back.
- Every state is handled: loading, empty, error, partial data, slow network and success.
- Copy is clear, short and consistent with the rest of the app. It goes through the app's translation mechanism where the app translates.
- Accessibility: keyboard use, focus order, labels, contrast and screen-reader names.

## Must fix
- A flow that breaks or ends in a dead end.
- A missing state that strands the user.
- A state or a text that differs from the design.
- A barrier that stops keyboard or screen-reader users.
- An unmet acceptance criterion.

Wording taste and polish are notes.

## Read
- `### review-ux` under `## Notes for steps` in `spec.md`.
- The group diff: `git -C <worktree> diff <group_base>..<branch>`. The full job diff, `git -C <worktree> diff <feature_branch>...<branch>`, is context only.
- The frames listed under `## Design` and `design/notes.md`, when they exist.
- The screens and components next to the change, for consistency.
- The translation files or copy sources the change touches.
- The `step_skills.ux` files whose `when` fits this repo. When none is listed, review against the repo's own docs and say so in one line under Notes.

## Rules
1. Do not edit code, commit or push.
2. You may run the app locally and walk the flow when `plan.md` says how. Stop every process you start.
3. Name the frame for every item that compares with the design.
4. Leave visual detail such as spacing, colour and typography to `review-ui`.
5. Without a design, say so in one line under Notes.
6. Do not raise a point again that a human or an earlier round settled.
