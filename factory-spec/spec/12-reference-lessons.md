# 12 Lessons from the reference implementation

The spec is reverse-engineered from a working, single-project factory (one skill, nine agents, a 3,000-line stdlib CLI, a dashboard, about 130 tests) that ran real multi-repo runs. These are the things it learned the hard way. Keep them.

## Shell and environment

- zsh does not split words in unquoted variables. `F="python3 factory.py"; $F run new` fails in zsh. Make the CLI an executable on PATH, so `$F` is one word.
- `set -- $VAR` to split arguments fails in zsh too (it exited 129 in a real run). Write every argument out in full in skill snippets.
- Shell state does not persist between Bash tool calls. Every snippet sets what it needs again, or uses absolute paths.
- A fresh git worktree has no dependencies installed. Every new feature or job worktree runs the repo's "Worktree setup" from the plan (lockfile install, generated files, the services the tests need, the toolchain path) before anyone works in it. Never symlink or reuse the main checkout's venv or `node_modules`: they may be stale. A failed setup goes into the producer's prompt (`setup: failed: <command>: <last error line>`) and into `retro-notes.md`.
- Remove job worktrees with `--force`: the setup leaves untracked env files.

## The loop

- WAIT FOR ALL. Never resume a producer while a gate of the same round still runs; mixed rounds produce reviews of code that no longer exists.
- Resume the **same** producer agent for every round and every merge conflict (store its name or id). It keeps its context and fixes faster. After a session restart, spawn a fresh one with the same prompt lines.
- Squash-merge each job into the feature branch with the job title as the commit message. The PR then shows one clean commit per job and no factory branch names.
- Parallel jobs leave notes for each other ("job B must handle the empty list"). Pass `upstream` job folders to dependants, and before the final full test run check the notes that were deferred to another job against what landed. A real run needed a hand check here.
- A job in another repo that depends on an endpoint starts only after the endpoint's job is merged into its own repo's feature branch; both specs carry the exact contract.
- Cap rounds (4 by default). After the cap, move on and put what is left in the PR's Open points; do not loop forever.

## Questions

- Free-form questions from agents were long and hard to answer between other work. A strict format (one decision, 2 to 4 lettered options, one recommended, at most 120 words, details folded) checked by code made them answerable with one letter. Refuse a question that breaks the format and ask the agent to rewrite it; log the refusal once per distinct text.
- Dashboard answers only reach the orchestrator through a background `question wait`. Keep exactly one running while questions are open, also while agents work.
- AskUserQuestion blocks the orchestrator's turn, so it cannot react to a finished agent. Use it only when nothing is running.
- Humans want to ask back before answering. A `clarify` state, a `REPLY:` file and a thread per question did that without code changes by the agent.

## Planning

- In one run the planner took about 58 minutes, most of it reading code it did not need. Point it at project knowledge first, code only to confirm, and give it a short reading list per candidate repo.
- Planners skip parts of their own spec template under load. A planner self-check (every spec has acceptance criteria, tests and, where needed, a contract; every acceptance criterion of the issue maps to a job) before it returns would have saved a round.
- Values the issue leaves open ("email address decided later") should become config placeholders, not questions.
- Non-code work (a mailbox, an agreement with another team) goes under "Outside the code", not into jobs.

## Inputs and safety

- Issue text, comments and designs are requirements data. Every agent is told to ignore instructions inside them.
- Short-lived signed image URLs from design tools are secrets: download, never print, never store.
- Strict config: the reference copied unknown profile keys to disk, so a token pasted into config by mistake would have been saved. Reject unknown keys at every level.
- A user-supplied issue key pattern as loose as `.*` let any text into branch names. Patterns must only allow key characters.
- The dashboard server must check `Host` and `Origin`, accept only JSON POSTs, and serve files only under the run folders.

## Integrations

- Read the PR template from its source on every run, never from memory, so a template change reaches the factory.
- Without credentials, Improve stopped. It should report PRs as unchecked and carry on, like everything else.
- The dashboard's PR poller needed a restart after a code host was configured. Reload config on every pass.
- The ticket key in the feature branch name (`feature/KEY-12-slug`) makes trackers link branches and PRs by themselves, with no write access.

## Cost

- A typical four-job run cost about 1.6 million tokens. Planner and producers on the strongest model, most reviewers on a mid model worked well; security and architecture gates on the strongest.
- Token counting from transcripts is exact and free, but depends on exact `key: value` prompt lines. Keep them machine-readable.

## Retrospective

- Evidence-only improvements (each tied to a file in the run) were useful; guesses were not. Few and good: three to five per run.
- `same_as` across runs turned repeated pain into one item with growing evidence.
- Humans decide improvements on a page; the factory never accepts or applies its own proposals inside a run.
