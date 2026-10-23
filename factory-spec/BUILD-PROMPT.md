# Build prompt

Paste everything below the line into a fresh Claude Code session started in an empty git repo that holds a copy of this folder as `factory-spec/`.

---

You build a general software factory from the spec in `factory-spec/`. Read `factory-spec/README.md` first, then every file in `factory-spec/spec/` in order. The spec is the source of truth. `factory-spec/prompts/` holds the skill and agent texts and `factory-spec/catalogue/` the built-in steps, pipelines and adapters: ship them as they are, and change them only where the code you build makes a sentence wrong (then fix both, and say so).

## Ground rules

- Python 3.10+ standard library only, SQLite, git, POSIX sh. No third-party packages, no build step.
- Claude Code is the only harness. Do not add abstractions for other agents or editors.
- No company, product or person names in anything you ship. Tool vendor names appear only inside their own adapter folders, in `examples/` and in `factory-spec/`. The neutrality test enforces this (spec 13): vendor words come from each adapter's `vendors` list, and the user's `[never]` names from an optional, gitignored `tests/denylist.local.txt`. No list of names is committed.
- No code comments. Put explanations in README files and keep the code readable through its names.
- Shell snippets must work in bash and zsh (spec 02, Platform rules).
- Never push, never open PRs, never touch repos outside this one. Commit only when the user asks.

## Phases

Stop at the end of each phase, report what works with the test output, and wait for the user's go.

0. **Decisions.** Read `spec/14-open-questions.md`. Ask the user about each item in one message, with your recommendation. Write the answers into `factory-spec/DECISIONS.md` and apply them to the spec before you build.
1. **Core state.** Package layout, `install.sh`, `factory init|doctor|config|paths`, schema and migrations, projects (08) with resolution (02), the catalogue loader and `catalogue show|check` (06), issues (08), `run new|status|show|list|archive|unarchive|delete`, `plan load` with full validation, the job and group state machine (`job ready|start|show|agent|round|next|stage|rework`, `step sync`, `gate sync`, `gate human`), questions, skips and events. Tests for all of it (13).
2. **Integrations and lifecycle.** Adapter loading, availability and the script protocol, the v1 adapters' scripts, `adapter new` and the adapter guide, `run pr|prs`, designs (`design links|target|check`), retro load, improvements with issue links, improve groups, usage sync and show with prices. Tests.
3. **Skill and agents.** The repo as a Claude Code plugin and marketplace (spec 02, Plugin), `factory skill install|uninstall|status|plugin`, `factory agents`, installing `prompts/` and catalogue agents. Check every CLI command and prompt key named in `prompts/` against the code; fix mismatches. The shell-syntax test.
4. **Dashboard.** `factory serve`, every API route, the pages in spec 10, the security rules. Tests for routes and guards.
5. **End to end.** Walk the acceptance list in spec 13 by hand with the user, in a scratch repo, one item at a time. Fix what breaks. Finish with the full test suite and a short report: what works, what is stubbed, what is left.

## How to work

- Small modules: `factory/db.py`, `projects.py`, `catalogue.py`, `issues.py`, `runs.py`, `jobs.py`, `questions.py`, `adapters.py`, `designs.py`, `retro.py`, `usage.py`, `cli.py`, `server.py`. One `FactoryError` with a plain message.
- Every command prints JSON; every state move logs an event; only these modules write the database.
- Write the tests with the code, not after. Run the full suite before you report a phase.
- When the spec is unclear, pick the simplest reading that keeps the principles in spec 01, write it into `DECISIONS.md`, and mention it in the phase report.
