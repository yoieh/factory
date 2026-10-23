# 14 Open questions

Decide these before or during phase 1. Each has a recommendation.

Decided on 2026-10-22: see `../DECISIONS.md`. Item 2 went the other way: the plugin is the primary install, and it carries the core.

1. **Product and CLI name.** `factory` may clash with other tools on PATH. Recommendation: keep `factory` in the spec, pick a final name before the first release, and make the CLI name one constant.
2. **Primary install for the skill.** Copies into `~/.claude` or a Claude Code plugin. Recommendation: copies first (simplest, works everywhere), plugin as an extra output of `factory skill plugin`.
3. **Which adapters in v1.** The spec lists `none`, `git`, `local`, `github`, `github-issues`, `figma` for v1 and `gitlab`, `bitbucket`, `jira`, `linear` for v1.1. Recommendation: move the adapters you use every day into v1, so the first real runs use them.
4. **Write-back to external trackers.** Off by default. The spec now posts one comment with the PR links when `"write": true` (09, Finish). Confirm, or drop write-back from v1. Recommendation: keep it, comment only, never a status change.
5. **Files jobs that land in a repo.** A `write-doc` result in `out/` or a docs PR. Recommendation: `files` jobs stay in `out/`; a docs change in a repo is a `code` job with `write-doc` as its producer.
6. **Code review required.** `require_code_review` defaults to true for code groups. Recommendation: keep it, and let a project turn it off.
7. **Human gate answers.** The approve option is A by convention in the step's `## Ask`. Recommendation: keep that convention, and treat any other answer as CHANGES with the answer as the note.
8. **Import of reference data.** Bring old runs, improvements and token totals across? Recommendation: no; start clean, and keep the reference read-only for history.
9. **Shared use.** One factory home per user and machine. A team-wide tracker stays the external tracker. Recommendation: confirm that the local tracker is a personal and factory backlog, not a team tool.
10. **Price table.** Built-in prices go stale. Recommendation: ship the table with a date, show the date on the dashboard, and let `prices.json` override it.
