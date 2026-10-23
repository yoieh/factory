# 04 CLI

`factory <group> <action> [args]`. Every command prints one JSON value on stdout on success; `--table` prints a human table where a command offers it, and `--json` is accepted everywhere as a no-op. Errors print `factory: <message>` on stderr and exit 1; argument errors and refused question formats exit 2 (`factory: question format: <problems>`). Commands that move state log one event per transition.

`<ref>` is `<run-id>/<job-id>`. `[--project p]` defaults to project resolution (02).

## Setup and health

| Command | Does |
|---|---|
| `init` | Create `$FACTORY_HOME`, `config.json` and `factory.db`. Idempotent. Other commands do the same when the home is missing, so the plugin needs no setup step (02). |
| `doctor [--project p]` | Check python, git, sqlite, PATH, home permissions, skill installs, adapter availability; print fixes and the `claude --add-dir` start command. |
| `config show` / `config set <dotted.key> <json>` | Read or change `config.json` (validated). |
| `paths <run-id>` | All paths of a run: `run_dir`, `plan`, `issue_file`, `design_dir`, `project_file`, `catalogue_file`, `retro_file`, `retro_notes`, feature worktrees per repo. |
| `agents` | `{"scope": "user"\|"repo"\|"plugin", "roles": {"planner", "worker", "reviewer", "retro", "improver": <subagent_type>}, "dedicated": {<agent name>: <subagent_type>}, "missing": [<agent names named by steps but not installed>]}` for the install the current cwd sees (repo scope wins over user scope, user scope over the plugin). |
| `serve [--port n]` | Start the dashboard on 127.0.0.1. |
| `demo` | Seed a demo project with fake runs and issues. |

## Projects, skill, catalogue, adapters

See 08 for `project ...`, 02 for `skill ...`, 06 for `catalogue show|check`, 07 for `adapter run` and `adapter new`.

## Issues

See 08 for `issue ...`.

## Runs

| Command | Does |
|---|---|
| `run new "<feature>" [--project p] [--repo r]... [--issue KEY-n] [--external-key K [--external-url U]] [--branch b] [--base b] [--parallel n] [--max-rounds n] [--confirm-plan] [--no-pr] [--session id]` | Create a run. Writes `project.json` and `catalogue.json` snapshots, `issue.md` for a local issue, records the project's skips. `--external-key` creates or updates a mirror issue (08) with the feature text as title and links the run to it; the orchestrator then writes `issue.md` from the adapter. With `--issue` or `--external-key` the plan is always confirmed, and the feature branch carries the external key when there is one, else the local key (`<branch_prefix><KEY>-<slug>`). A repo without an `origin` remote is allowed: its base is the local default branch, nothing is fetched, and the code host's `push` counts as unavailable for it. Without `--repo` the planner picks repos from the project. `no_pr` is set when the code host cannot `push` for any repo of the run (a host with `push` but no `open_pr` pushes and leaves the PR to a person); a repo that alone cannot push (no `origin`) gets `can_push: false` in `repos`, and Finish skips its push and PR with a skip. Returns `id`, `feature_branch`, `run_dir`, `plan`, `issue_key`, `issue_file`, `repos`, `project_file`, `catalogue_file`, `skipped`, `no_pr`, `confirm_plan`. |
| `run session <run-id> [id]` | Add a resumed orchestrator session (for token counting). |
| `run status <run-id> <status>` | Move the run (03). `finishing` and `done` are refused while a job is neither `done` nor `dropped`. |
| `run rounds <run-id> <n>` | Change the round cap of groups not yet passed. |
| `run pr <run-id> <url> --repo r` | Store a PR url (validated against the code host's `pr_url` pattern when it has one). Links it to the run's issue and clears the run's `no_pr`. |
| `run prs [<run-id>] [--force]` | Read PR states through `pr_state`; skips final states unless `--force`. Moves issues to `done` when all PRs merged. |
| `run show <run-id>` | Run, repos, jobs with groups and verdicts, questions with threads, events, skips, history. |
| `run list [--project p] [--archived] [--table]` | Runs with status, open questions, cost. |
| `run archive <run-id> [--force]` / `run unarchive <run-id>` | Hide a done or failed run; remove its worktrees and local job branches; keep files and numbers. |
| `run delete <run-id> [--force]` | Remove a run for good, with files, worktrees and local branches. Refuses with accepted, unapplied improvements. |

## Plan and jobs

| Command | Does |
|---|---|
| `plan load <run-id>` | Read `plan.md`. Returns `{"question": id}`, `{"question_error": [...], "path"}`, an error listing every validation problem (06), or `{"jobs": [...], "repos": [...]}` with per job `missing: ["spec.md"]` when a spec is missing, and per repo `path`, `base_branch`, `feature_worktree`, `can_push`. Refuses once a job has started. |
| `job ready <run-id>` | `ready`: queued jobs whose `depends_on` are `done`, capped so running producers never exceed `parallel`. `blocked`: queued jobs that depend on a dropped job. |
| `job start <ref>` | `queued → running`, group 1 round 1 phase produce. Returns `job_dir`, `kind`, `repo`, `repo_path`, `branch`, `worktree`, `feature_branch`, `feature_worktree`, `out_dir`, `sources`, `setup_hint` (the repo's Worktree setup from plan.md), and `group`: `{position, step, step_file, group_dir, round_dir, agent, model, round, base_sha}`. `base_sha` is the feature branch head (`git rev-parse`) for group 1, and the job branch head when `job next` starts a later group. |
| `job show <ref>` | Everything `job start` returns for the current group (`job_dir`, `kind`, repo and worktree fields, `out_dir`, `sources`, `setup_hint`, `group`), plus `stage`, `conflict`, all groups (each with `group_dir`, `round_dir` of its latest round, `base_sha`, `agent`, `phase`, `round`), verdicts, `upstream` (job dirs of `depends_on`), `previous` (earlier group dirs) and questions. |
| `job agent <ref> <name-or-id>` | Store the current group's producer agent (for SendMessage). |
| `job round <ref>` | Next round of the current group, phase produce. Refuses at `max_round`. Returns the new `round` and `round_dir`. |
| `job next <ref>` | After the current group is `passed` or `capped`: the next group (phase produce, round 1, returns its `group` as in `job start`), or stage `merging`/`delivering` when it was the last. |
| `job stage <ref> <stage>` | Only the moves 03 allows that no other command makes: `merging → done`, `delivering → done`, `merging → running` (merge conflict). A conflict move sets the last group's phase to `produce`, keeps its round and marks the job `conflict`; the next `step sync` with DONE moves the job straight back to `merging`, without gates and without a new round. |
| `job drop <ref> "<reason>"` | An unfinished job to `dropped`: logs the reason, writes it to `retro-notes.md`, and returns `job show` plus `dependants`, the jobs that waited on it. |
| `job rework <ref> "<note>" [--group n]` | A `done` or `dropped` job back to `running` at its last group (or group `n`) as 03 describes (round + 1, later groups pending, `base_sha` reset), writes `round-<n>/rework.md`, reopens the run. Returns the same as `job show`. |

## Steps and gates

| Command | Does |
|---|---|
| `step save <ref> <path> [--by who]` | Store a file an agent returned as text because the harness refused its write (05, Refused writes), read from stdin: the group's `output.md`, a gate file of the current round, or a file under a files job's `out/`. Anything else is refused. Logs `step.save`. |
| `step sync <ref>` | Read the current group's `output.md`. Returns `status`: `DONE` (phase moves to gate; returns `gates`: per gate `{gate, agent, model, gate_file, review_file, round_dir, human}`; with no gates the group is `passed`), `BLOCKED` (with `reason`), `QUESTION` (stores it, returns `question`; or `question_error`), `MISSING`, `INVALID`. For a `conflict` job, `DONE` returns `{"status": "DONE", "stage": "merging"}` and no gates. |
| `gate sync <ref>` | Read the round's gate files. Returns `complete` (all present), `outcome` (`PASS`, `CHANGES`, `PENDING`), per gate `verdict`, `questions`, `question_errors`, `human` (gates waiting for a human, with their question text). On `PASS` the group is `passed`; on `CHANGES` at `max_round` it is `capped` and the result lists the open points. |
| `gate human <ref> <gate> pass\|changes [--note "<text>"]` | Write a human gate's file and record the verdict. |

## Questions

| Command | Does |
|---|---|
| `question ask <run-id> "<text>" [--job j] [--by who] [--path p] [--plain]` | Store a question (format checked unless `--plain`). Storing is idempotent: the same `path` and text hash (for asks without a path: run, job, asker and text hash) returns the existing id. `plan load`, `step sync` and `gate sync` store file questions the same way, so a sync that runs again never makes a second question. |
| `question answer <id> "<text>" [--delivered] [--by <name>]` | Store an answer (`answered`). `--delivered` when the orchestrator acts on it now. `--by` names who wrote it for the user (default `human`; for example an assistant the user lets answer), stored as the message author. |
| `question clarify <id> "<text>" [--delivered] [--by <name>]` | Ask back (`clarify`). Returns `reply_file`. `--delivered` when the orchestrator relays it now. `--by` as for `question answer`. |
| `question reply <id> [--text "<t>"]` | Store the agent's `REPLY:` file (or a text) and set the question `open` again. |
| `question resolve <id>` | `answered → resolved` after the agent got the answer. |
| `question list [--run r] [--status s]...` | Questions with threads. |
| `question wait <run-id> [--timeout 1800]` | Block until the run has an undelivered `answer` or `clarify` message; return those items and mark them delivered. Each item: `id`, `job_id`, `asked_by`, `status`, `kind` (`answer` or `clarify`), `text` (the human's text), `by` (the message author: `human`, `dashboard` or the `--by` name) and, for `clarify`, `reply_file`. Returns `[]` on timeout. `question clarify --delivered` and `question answer` from the session's own chat (`--delivered`) mark their message delivered at once. |

## Skips and events

| Command | Does |
|---|---|
| `skip add <run-id> <dependency> "<effect>" [--pr] [--job j]` | Record a skip: event, one line in `retro-notes.md`. `<dependency>` is `<slot>.<capability>` for adapters (`code_host.push`, `code_host.open_pr`, `design.fetch_design`, `tracker.fetch_issue`) or a plain name for the rest (`project`, `step-agent`, `setup`). The same dependency, job and effect is one skip. |
| `skip list <run-id> [--pr]` | The run's skips. |
| `event <run-id> <kind> [--job j] [--payload json]` | A free event, for example `merge.conflict`. |

## Designs

| Command | Does |
|---|---|
| `design links <path>` | Design links in a text file, by the project's design adapter `design_url` pattern. |
| `design target <run-id> --name "<frame>"` | The `png` and `md` paths for a frame (safe file names, unique). |
| `design check <run-id>` | Remove downloads that are not PNG (magic bytes), strip `scrub` patterns from design md files, list the designs. |

## Retro, improvements, usage

| Command | Does |
|---|---|
| `retro load <run-id>` | Read `retro.md`'s json into improvements (05 rules). Updates the run's improvements in place on a second load. |
| `improvement list [--run r] [--project p] [--status s]` | Improvements with `seen_in`. |
| `improvement decide <id> open\|accepted\|rejected\|done [--note "<why>"]` | A human's decision. Accept creates the linked issue (08). |
| `improvement start [--id n]... [--factory-base b]` | Group accepted items without a PR per repo path and base branch; write `items.md` and `project.json` per group; return per group `repo` (the repo name, `factory` for the factory source), `repo_path`, `remote_url`, `base_branch`, `branch` (`chore/improvements-<batch>`), `worktree`, `items_file`, `report_file`, `pr`, `ids`, plus per group `project_file` (the resolved config of the item's project, which gives the code host and the repo's `setup`), `process` items and `skipped` items with reasons. `factory` items go to `config.json` `factory_source` (current branch unless `--factory-base`), using the code host and setup of the `factory_project` project when it links that checkout as a repo, else no code host and no setup; without a factory source they are skipped as "no factory source". |
| `improvement pr <id>... --url u --branch b` | Record the PR applying items. |
| `improvement prs [--check]` | Open PRs of accepted items. With `--check`, read each PR's state through its project's `pr_state` (as `run prs` does) and return `state` per PR (`unchecked` with a reason when there is no `pr_state` or credentials are missing); it changes no item. |
| `usage sync [<run-id>]` | Read new transcript lines into `usage` (11). |
| `usage show <run-id>` | Tokens and estimated cost per node, job, round and total. |
