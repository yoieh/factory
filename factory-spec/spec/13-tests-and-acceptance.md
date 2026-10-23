# 13 Tests and acceptance

Stdlib `unittest` only: `python3 -m unittest discover -s tests`. Tests never call Claude, the network or a real code host. Agents are simulated by tests writing their output files.

## Fixtures

- A temp `$FACTORY_HOME` and a temp `HOME` per test case.
- Temp git repos with an `origin` (a bare repo in the temp folder), a default branch and one commit.
- A fake adapter folder with a `pr_state` script that prints JSON from an env var, and one that exits 3.
- Sample Claude Code transcripts (orchestrator and two subagents) for usage sync.
- Tests that need a vendor's name (an adapter's own scripts) live in that adapter's folder, `catalogue/adapters/<name>/tests/`, and the suite loads them, so `tests/` names no vendor. They fake the vendor's CLI with a script on PATH.

## Unit and flow tests

| Area | Cases |
|---|---|
| Install | `install.sh` into a temp prefix; `factory init` twice; a first command creates a missing home; `doctor` output; `skill install --user` and `--repo` write the files and record installs; `skill status` shows drift after a core version bump. Plugin: `plugin.json` and `marketplace.json` are valid, `plugin.json` `version` equals the core version, `skills/` and `agents/` match `prompts/`; the CLI run from a plugin folder copies the core to `core/<version>/` and moves `current`. |
| Config | Unknown keys refused in `config.json` and `project.json` at every level; paths expanded; adapter `settings` enforced; a token-looking key refused. |
| Projects | add, link-repo (remote and default branch detection), set, show with availability, pin; resolution order (each of the 6 steps), ambiguity error, ad-hoc project with its skip. |
| Catalogue | front matter parser; override order (5 levels); `catalogue check` errors (bad role, unknown step in a template, missing dedicated agent); snapshot written at `run new`. |
| Plan load | valid plan; each validation rule in 06 fails with a clear message; all problems listed at once; questions and question format errors; missing specs; refused after a job started. |
| Job flow | `job ready` respects `depends_on` and `parallel`; `job start` paths; `step sync` for DONE, BLOCKED, QUESTION, MISSING, INVALID; `gate sync` WAIT FOR ALL, PASS, CHANGES, cap; `job round` refused at cap; `job next` through 2 groups to merging and to delivering; `previous` and `upstream`; rework reopens a done job and its run; merge-conflict move; `job drop` with `blocked` dependants and the finishing guard; `step save` stores only the agent's own files. |
| Human gates | `gate sync` reports `human` with a valid question; `gate human pass` and `changes` write the file and drive the round logic. |
| Questions | format check (each rule), refusal logged once per text, ask, answer, clarify, reply, resolve, wait (answered, clarify, timeout), threads. |
| Issues | add, keys per project, list filters, set, edit, link, import and re-import (update), mirror status rule, automatic moves on run start, first PR and all PRs merged; improvement accept creates a linked issue, reject closes it. |
| Adapters | availability rules; script protocol (placeholders, timeout, exit 0, 3, other, bad JSON); env only from listed names; no secret in stdout, events or files; pattern rules (empty match and unsafe key characters refused); a script that is not built reports `not built`; `vendors` validated; `adapter new` per slot writes a skeleton that `catalogue check` flags until its placeholders are filled, then accepts. |
| Runs | `run new` with and without issue, external key, `no_pr` from code host capabilities, skips recorded; status machine; archive, unarchive, delete with its refusal; `run prs` with final states. |
| Designs | `design target` safe names; `design check` removes non-PNG files and scrub patterns. |
| Retro | `retro load` valid, too many items, bad target, bad key, `same_as`, second load in place. |
| Improve | `improvement start` groups per repo and base, `factory` items without a factory source skipped, `process` items listed; `improvement pr`, `prs`. |
| Usage | sync from sample transcripts (offsets, once per message, node and round from prompt lines, resumed producer split at round events); `usage show`; unknown model; unreadable lines skipped. |
| Migrations | open a database at each older schema version and migrate it without data loss. |
| Dashboard | every GET and POST route; Host and Origin checks; JSON-only POST; path guard (traversal, symlink out, wrong suffix); PNG magic check; redaction of project responses; text limits. |
| Neutrality | A test greps every file in the repo that git does not ignore, tracked or not, with two checks. `[vendors]` always runs: each word in an adapter's `vendors` list (07) may appear only in the folders of the adapters that list it, in `examples/` and in `factory-spec/`. `[never]` runs only when the optional, gitignored `tests/denylist.local.txt` exists: under a `[never]` line it lists company, product and person names that may appear nowhere; when the file is missing, this check is skipped with a message. Entries match case-insensitively as whole words (`cat` does not match `catalogue`); an entry ending in `-` or `_` matches as a prefix. Any other hit fails the test with the file, line and word. No list of names is committed. |
| Shell | Every fenced `sh` block in `SKILL.md`, the agents and the adapters is parsed by `sh -n` and `zsh -n` when zsh exists, after each `<placeholder>` is replaced by a plain word. |

## Acceptance of the build

The build is done when:

1. A fresh machine user can add the core's repo as a plugin marketplace, install the `factory` plugin, open Claude Code in any git repo and run `/factory <repo> "<small feature>" --no-pr` to a `done` run with an ad-hoc project, no integrations configured, and every missing integration shown as a skip. The same works without the plugin, after `sh install.sh`, `factory init` and `factory skill install --user`.
2. The same with a project of two repos, a code host adapter that can push and open PRs, and the local tracker: `/factory SHOP-1` opens one draft PR per repo, the issue moves to `in_review`, then `done` when the PRs are merged.
3. A `bug-fix` pipeline (reproduce, then fix with `verify`) and a `research` files job run in the same run.
4. A step file added to `<repo>/.factory/steps/` is offered to the planner and used, without code changes.
5. The dashboard shows the run with per-job pipelines, answers a question, and edits an issue.
6. The whole test suite passes, including the neutrality test.
