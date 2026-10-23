---
name: review-ui
role: gate
kinds: [code]
agent: factory-reviewer
summary: Checks layout, components, tokens and responsiveness, against the design when there is one.
when: The job changes the visible layout, components or styles of a user interface.
requires: []
skills_key: [ui]
version: 1
---

## Focus
- With a design: when the spec has a `## Design` section, compare the implementation with each listed frame (`.png` and `.md`) and `design/notes.md` when it exists: layout, spacing, colours, typography, texts and states.
- Without a design: the change matches the screens around it.
- The change uses the repo's existing components, theme tokens and layout patterns, not new one-off styles.
- Colours come from the theme. When a `step_skills.ui` file gives brand colours, they match it.
- Spacing, typography and alignment match the screens around the change.
- The layout works at every breakpoint the app supports.
- No hard-coded sizes or colours where tokens exist.

## Must fix
- A visible break or a broken layout at a supported breakpoint.
- A difference from the design that a user would notice.
- A hard-coded value where the repo's rules require a token.
- An unmet acceptance criterion.

Pixel-level detail and taste are notes.

## Read
- `### review-ui` under `## Notes for steps` in `spec.md`.
- The group diff: `git -C <worktree> diff <group_base>..<branch>`. The full job diff, `git -C <worktree> diff <feature_branch>...<branch>`, is context only.
- The frames listed under `## Design` and `design/notes.md`, when they exist.
- The repo's theme, tokens and shared components.
- The `step_skills.ui` files whose `when` fits this repo. When none is listed, review against the repo's own docs and say so in one line under Notes.

## Rules
1. Do not edit code, commit or push.
2. Map the design's values to the repo's tokens. Ask for the token, not the raw value.
3. You may run the app locally to look at the screens when `plan.md` says how. Stop every process you start.
4. Name the frame for every item that compares with the design.
5. Leave flows, states and copy to `review-ux`.
6. Without a design, say so in one line under Notes.
7. Do not raise a point again that a human or an earlier round settled.
