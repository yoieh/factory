# 11 Usage, retrospective and improve

## Token usage

The factory counts tokens per node from the transcripts Claude Code writes anyway; nothing runs inside the agents.

- `run new --session <id>` and `run session` tie orchestrator sessions to a run. The skill passes `${CLAUDE_SESSION_ID}`; an empty value falls back to `$CLAUDE_CODE_SESSION_ID`. Session ids match `^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$`.
- `usage sync` reads new lines from `~/.claude/projects/*/<session-id>.jsonl` (orchestrator, node `orchestrator`) and `~/.claude/projects/*/<session-id>/subagents/agent-*.jsonl` (subagents). It reads each file from its stored offset and counts each assistant message once (by message id, keeping a small `recent` set for the boundary).
- A subagent's run, job, node and round come from the `key: value` lines of its first prompt: `run_dir` (run id), `job_dir` (job id), `node`, `round`. A resumed producer is split into rounds at the round-start times in `events` (`job.round`).
- Fields: `input`, `cache_write_5m`, `cache_write_1h`, `cache_read`, `output`, per model.
- Cost: a built-in price table per model id (dollars per million tokens: input, output, cache read; cache writes priced as input times 1.25 for 5 minutes and 2 for 1 hour), with the date its prices were checked, overridable by `$FACTORY_HOME/prices.json` (which carries its own date). `usage show` and the dashboard show that date next to costs. An unknown model counts tokens and shows cost as unknown.
- The transcript format is internal to Claude Code. When a line cannot be read, sync skips it; when nothing can be read, the dashboard shows "–". Sync never fails a run.
- Improve runs outside any run; its tokens are not counted.

## Retrospective

- After `done` or `failed`, the orchestrator spawns `factory-retro` (prompt keys in `prompts/agents/factory-retro.md`).
- It reads the plan, issue, designs, every job's spec, outputs, gate files and rework notes, the PR texts, `retro-notes.md` (skips included), `run show`, `usage show` and `improvement list` (to avoid repeats), and the files a finding points at.
- It writes `retro.md` with at most 8 improvements, each with evidence from this run (05). Targets come from the project's `improvement_targets`.
- `retro load` stores them; `same_as` adds the run to an earlier item's `seen_in`. A second load after a rework updates the run's items in place.
- What it looks for, strongest first: human answers and rework notes; rounds with CHANGES that a better spec, plan or step file would have prevented; items left at a cap and BLOCKED steps; merge conflicts and job splits that made jobs wait; gates with nothing to say or missing where a later round found a problem; pipeline choices that did not fit (a gate that never mattered, a missing `verify`); cost hotspots; `retro-notes.md` lines; knowledge the agents had to dig out that project knowledge should hold; gaps in the issue or design.

## Improvement targets

| Target | Applied to | Notes |
|---|---|---|
| `factory` | `config.json` `factory_source` | The skill, agents, catalogue, core. Skipped without a factory source. |
| `repo` | a project repo | Its docs, tests, setup. No factory words in the repo. |
| `knowledge` | the repo named by the target | The project knowledge. |
| `process` | nothing | How issues and designs are written; listed for the human. |
| custom | as configured | A project may add targets with `repo` and `base`. |

## Improve

`/factory improve [<id>...] [--factory-base b]`, in its own session:

1. Merged PRs first: `improvement prs --check` reads each PR's state through `pr_state`. `MERGED`: `improvement decide <id> done --by orchestrator` for each item of that PR. `DECLINED` or `SUPERSEDED`: report; a human decides. `OPEN`: report as waiting. No `pr_state` or missing credentials: report as unchecked and carry on.
2. `improvement start`: groups per repo and base branch, `items.md` per group.
3. Per group: `git -C <repo> fetch origin`, `worktree add` a `chore/improvements-<batch>` branch from `origin/<base>` (without an `origin`: no fetch, branch from the local base), then the repo's `setup` from project config in the worktree; pass the result as the improver's `setup`.
4. One `factory-improver` per group, all in one message, background.
5. Read `report.md`: BLOCKED or no commits: remove the worktree and branch, report. Else rerun the test command; on failure resume the improver once; still failing: push nothing, report.
6. Write `pr.md` (pr_template or plain), no factory words in non-factory repos.
7. Without `push`: keep worktree and branch, report them. With `push` but no `open_pr`: push and report the branch. Else push, open a draft PR, `improvement pr <ids> --url --branch`, remove the worktree (keep the branch).
8. Report per group: PR url and items, skipped items with reasons, `process` items with their proposals.

An item is `done` only when its PR is merged (step 1) or a human marks it.
