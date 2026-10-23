# factory

A software factory for Claude Code. It turns an issue or a feature text into reviewed changes: a planner splits the work into small jobs, each job runs a pipeline of steps from a catalogue, agents work in their own git worktrees, and the results land in one feature branch per repo. The spec it is built from is in `factory-spec/` (start with `factory-spec/README.md`); the decisions taken during the build are in `factory-spec/DECISIONS.md`.

This repo is the core: the `factory` CLI, the SQLite schema, the built-in catalogue, the `/factory` skill and its five agents, and the tests. It is also a Claude Code plugin and a one-plugin marketplace. It was built in phases (`factory-spec/BUILD-PROMPT.md`): the core state, the integrations and lifecycle, the plugin with the skill and agent installs, the dashboard, and a walk through the acceptance list end to end.

## Requirements

Python 3.10 or newer (standard library only), git, SQLite 3.35 or newer (the one inside Python), and a POSIX shell. macOS and Linux; Windows through WSL.

## Install as a Claude Code plugin

In Claude Code:

```text
/plugin marketplace add yoieh/factory
/plugin install factory@factory
```

Claude Code 2.1.275 and later also take it in one step: `/plugin install factory --marketplace yoieh/factory`. `yoieh/factory` is the repo's `<owner>/<repo>` short form; its full clone URL works too, and `#<branch or tag>` after either pins a version. To work on the factory itself, add the path of a local checkout instead (start a relative path with `./`): the plugin then loads from that folder, so edits need no reinstall.

Claude Code does not update plugins from this marketplace by itself unless you turn auto-update on in the **Marketplaces** tab of `/plugin`. To update by hand, choose **Update now** on the plugin in the **Installed** tab, or run `claude plugin update factory@factory` in a shell. An installed copy only changes when `version` in `.claude-plugin/plugin.json` changes, so every release bumps it.

The plugin brings the `/factory` skill (its full name is `/factory:factory`; plain `/factory` works while no other command uses that name), the five agents (`factory:factory-planner`, `factory:factory-worker`, `factory:factory-reviewer`, `factory:factory-retro`, `factory:factory-improver`) and the `factory` command, which Claude Code puts on the Bash tool's PATH. Nothing else to set up: the first command creates the factory home. State never lives in the plugin folder, because that folder changes with every version and Claude Code deletes plugin data on uninstall; it lives in `~/.factory`.

The first time the plugin's CLI runs, it copies itself to `~/.factory/core/<version>/` and points `core/current` at it. The terminal command and the dashboard then run the same core as the plugin. To get the terminal command, run `sh install.sh` once from the plugin folder; `factory doctor` prints that command when the shim is missing or runs another version.

`factory agents` prints the `subagent_type` the skill uses for each role. A repo-scope copy wins over a user-scope copy, and a user-scope copy over the plugin.

### Copies instead of the plugin

For a repo owner who wants the skill committed in the repo, or a machine without the plugin:

```sh
factory skill install --user                       # ~/.claude/skills/factory and ~/.claude/agents/factory-*.md
factory skill install --repo <path> [--project p]   # <repo>/.claude/skills/factory and <repo>/.claude/agents/
factory skill status                                # every recorded install, its version and drift from the core
factory skill uninstall --user | --repo <path>
factory skill plugin <dir>                          # a plugin folder with only the skill and agents; the CLI comes from the core install
```

Installs copy files (no symlinks), so a repo owner can commit them. Each install also copies the dedicated agents that step files name, when their file is in a catalogue `agents/` folder. `factory skill status` and `factory doctor` show an install whose files or version no longer match the core.

## Install in a terminal

```sh
sh install.sh                 # copies the core to ~/.factory/core/<version>/, links core/current, writes ~/.local/bin/factory
sh install.sh --prefix ~/bin  # the command goes into ~/bin instead
factory init                  # optional: any command creates a missing factory home
factory doctor                # checks python, git, sqlite, PATH and the home folder, and prints fixes
```

`FACTORY_HOME` moves the factory home (default `~/.factory`). `FACTORY_CORE=<checkout>` makes the installed command run a git checkout of the core instead of `core/current`, which is how the factory works on itself. `bin/factory` in a checkout runs directly too.

## The factory home

```
~/.factory/
  config.json                    global settings (factory config show, factory config set <key> <json>)
  factory.db                     SQLite in WAL mode, written only by the CLI
  core/<version>/ core/current   installed cores
  catalogue/                     your own steps, pipelines, adapters and agents
  projects/<name>/project.json   project settings
  issues/<KEY>-<n>.md            issue bodies
  runs/<run-id>/                 run files: plan, specs, outputs, gate files, notes
  worktrees/<run-id>/            feature and job worktrees
```

Config files are strict: an unknown key at any level is an error, so a token pasted into the wrong place is refused instead of saved. Credentials never go into config; adapters read them from the environment or from credential files.

## Commands

Every command prints one JSON value. Errors print `factory: <message>` on stderr and exit 1; argument errors and refused question formats exit 2. `--json` is accepted everywhere and changes nothing; `--table` prints a table for `run list`, `issue list`, `project list` and `question list`. `factory <group> --help` lists the actions of a group.

| Group | Commands |
|---|---|
| Setup | `init`, `doctor [--project p]`, `config show`, `config set <dotted.key> <json>`, `paths <run-id>`, `agents`, `serve [--port n]`, `demo` |
| Skill installs | `skill install --user | --repo <path> [--project p]`, `skill uninstall`, `skill status`, `skill plugin <dir>` |
| Projects | `project add|link-repo|unlink-repo|set|show|list|here|pin|archive` |
| Catalogue | `catalogue show [--project p]`, `catalogue check [--project p]` |
| Adapters | `adapter run <name> <capability> [--project p] [--repo r] [--<placeholder> <value>]...`, `adapter new <name> --slot tracker|code_host|design [--dir d]` |
| Issues | `issue add|list|show|set|edit|link|import|sync` |
| Runs | `run new|session|status|rounds|pr|prs|show|list|archive|unarchive|delete` |
| Plans and jobs | `plan load`, `job ready|start|show|agent|round|next|stage|rework|drop` |
| Steps and gates | `step sync|save`, `gate sync`, `gate human` |
| Questions | `question ask|answer|clarify|reply|resolve|list|wait` |
| Connected agents | `agent add|list|show|remove`, `inbox [--project p] [--as name]`, `events [--since n] [--wait] [--as name]` |
| Designs | `design links <path>`, `design target <run-id> --name <frame>`, `design check <run-id>` |
| Retrospective | `retro load <run-id>`, `improvement list|decide|start|pr|prs` |
| Usage | `usage sync [<run-id>]`, `usage show <run-id>` |
| Skips and events | `skip add|list`, `event` |
| History | `legacy import <dir> --project p [--repo r] [--issues <map.json>] [--dry-run]` |

The full reference is `factory-spec/spec/04-cli.md`, with projects and issues in `08`, the catalogue and pipelines in `06` and the file formats in `05`.

### Which project a command uses

`--project`, then `FACTORY_PROJECT`, then a `.factory/project` file in the repo (`factory project pin`), then a project that links the current repo by path or by its `origin` remote, then `default_project` in `config.json`. When none matches, `run new` registers an ad-hoc project for the current repo and records that as a skip. `factory project here` says which step matched and why.

### A run, step by step

```sh
factory project add shop --key SHOP
factory project link-repo shop ~/src/shop/api
factory run new "Add a lead endpoint" --project shop --repo api
# the planner writes runs/<run-id>/plan.md and jobs/<job-id>/spec.md
factory plan load <run-id>
factory run status <run-id> running
factory job ready <run-id>
factory job start <run-id>/<job-id>
# the producer writes groups/01-build/output.md
factory step sync <run-id>/<job-id>
# the gates write groups/01-build/round-1/<gate>.md
factory gate sync <run-id>/<job-id>
factory job next <run-id>/<job-id>
# the orchestrator squash-merges the job branch into the feature branch
factory job stage <run-id>/<job-id> done
```

The orchestrator skill does all of this; the commands are listed so the state moves are easy to follow. Each move logs an event (`factory run show <run-id>` lists them).

A job you give up is dropped with `factory job drop <run-id>/<job-id> "<reason>"`; a run finishes only when every job is done or dropped. In auto mode Claude Code may refuse a subagent's write of a report-like file such as `out/findings.md`. The agent then returns the file as text, and the orchestrator stores it with `factory step save`, which accepts only that agent's own files.

### History from the old factory

`factory legacy import <dir> --project <p>` brings the history of the old board-based factory (the reference this core was built from) into the dashboard. `<dir>` is its `factory/` folder, the one with `board.json`, `jobs/`, `retros/` and `backlog.md`. Every merged job becomes a finished run with one job and one `build` group: its rounds and verdicts come from the `round-<n>/review-<gate>.md` and `decision.md` files (the approver's decision is the `approve` gate, a human rework the `human` gate), and its files are copied into the run folder. Answers in `answers.md` become answered questions. Every retro proposal becomes an improvement on the run of the last job the retro looked at, with the status its `(retro <id>#<n>)` tag has in `backlog.md` or `backlog-archive.md`: `[x]` done, `[ ]` accepted, `[?]` open, `[-]` rejected with its reason. `--issues` takes a JSON map from issue keys to `{"old": <job id or backlog text>}`, so an accepted proposal links the issue it already became. `--dry-run` counts and changes nothing, and a second import adds only what is new.

## Connecting agents

Any outside agent can drive the factory for you: a voice assistant, another coding agent, a script. The factory does not know which agents exist or what they are. It knows a connected agent only by the name the agent passes, and what that agent may approve for you is set up in the agent, not here.

```sh
factory agent add helper --description "Answers while I am away"   # optional: the first --by or --as registers the name too
factory events --wait --as helper          # blocks until something happens; returns the events and a cursor
factory events --since <cursor> --wait --as helper
factory inbox --as helper                  # everything that waits for you, across runs
factory question answer <id> "yes" --by helper
```

`factory inbox` lists the open questions of every run, each with its `kind` (`question`, `plan_confirm` or `human_gate`), the run and job, the question line, the options with the recommended one, and the exact command to answer or ask back, plus the improvements waiting for a decision. `factory events` follows the event log from a cursor, so an agent waits on one call instead of watching every run. An answer stored with `--by <name>` counts as yours: the orchestrator acts on it like an answer in its own chat. Over the network, the dashboard serves the same as `/api/inbox`, `/api/events` and `/api/agents`, and its answer and follow-up POSTs take `"by": "<name>"`.

The **Agents** page of the dashboard lists every connected agent with when it was last seen and what it answered. `factory agent remove <name>` forgets one; its answers keep its name.

## Dashboard

```sh
factory serve                 # http://127.0.0.1:8765/, or the port in config.json dashboard.port
factory serve --port 9000 --quiet
factory demo                  # a demo project with runs, questions, issues and improvements to look at
```

One local page, served by the standard library and built from one self-contained file (`factory/dashboard.html`: no build step, nothing loaded from the network). It reads through the same functions as the CLI and writes only answers, follow-ups, improvement decisions, issue edits and archive flags.

- **Inbox**: the open questions and plan confirms, one at a time. Click an option to put its letter in the answer box; Ctrl or Cmd + Enter sends; **Ask back** sends a follow-up instead. Left and right arrows walk the questions. A plan confirm shows the jobs with their pipelines and an Approve button. The orchestrator gets the answer through its `question wait`.
- **Runs**: the loop graph (the run's phases, with one lane per job: a box per group, its gates as chips that turn green or red per round, the job's token and a `?` on a waiting job), a list, and a timeline. The right pane shows repos and PR states, cost by node with the date of the price table, skips, run files, answered questions and events; with a job open, its pipeline, verdicts and files. Opening a run counts its tokens (`usage sync`).
- **Issues**: filter, create, change status, priority, title and labels, edit the body, and copy the `/factory <KEY>` command that starts a run in Claude Code.
- **Projects**: repos, integrations with availability, knowledge, skill installs and drift, and the catalogue with where each step comes from. Read-only.
- **Improvements**: evidence and proposal per item; accept, reject, mark done or send back to review, with a note.
- **Agents**: the connected agents, with when they were connected and last seen, and their latest answers. Read-only.

To reach the dashboard from other devices on a tailnet, keep the server on 127.0.0.1 and put `tailscale serve` in front of it, then list the machine's tailnet name:

```sh
tailscale serve --bg --https=8765 http://127.0.0.1:8765
factory config set dashboard.hosts '["<machine>.<tailnet>.ts.net"]'
factory serve                 # restart it to read the new names
```

The dashboard is then at `https://<machine>.<tailnet>.ts.net:8765/` for the tailnet only. A name in `dashboard.hosts` is accepted on any port, and POSTs from `http://` or `https://` on that name count as the dashboard's own. Anyone who can reach that name can answer questions and edit issues, so list only names that only your devices reach.

The page polls every 3 seconds while visible and every 30 seconds when hidden. A background thread reads the PR states of open and unchecked PRs every 5 minutes through the code host's `pr_state`, with the project config read fresh each time. The theme follows the system, with a toggle the browser remembers.

The server binds 127.0.0.1 only and answers only requests whose `Host` is `127.0.0.1:<port>` or `localhost:<port>`, or a name listed in `config.json` `dashboard.hosts`, which stops DNS rebinding. POSTs must be `application/json`, and a POST with an `Origin` other than the dashboard's own is refused. Files are served only as `.md` or `.json` under `runs/`, `improve/` and `issues/`, and `.png` under `runs/` when the file starts with the PNG signature; paths are resolved first, so `..` and symlinks that leave those folders are refused. Answers and notes are limited to 4000 characters and issue bodies to 100 000. Project responses leave out paths outside the project's repos and the factory home, and show adapter settings marked `secret` as `***`. Markdown is rendered into page elements with every text escaped; only the `<details>` and `<summary>` of questions are rebuilt.

## Adapters

Trackers, code hosts and design tools plug in through adapters, at most one per slot (`factory-spec/spec/07-integrations.md`). An adapter is a folder with `adapter.json` (what it can do, its settings and patterns) and `adapter.md` (what the orchestrator does for each capability). The built-in ones are in `catalogue/adapters/`; `local` (the central issue list) and `none` (no code host) are the defaults.

Each capability says who does it (`via`). The orchestrator does `mcp`, `cli`, `skill`, `git` and `file` capabilities by following `adapter.md`. The core does `core` capabilities itself, and runs `script` capabilities with the project's settings: `factory adapter run <name> <capability>` fills the `{placeholders}` of the command, runs it in the adapter folder with a 30 second timeout, and prints its JSON. A script that exits 3 means credentials are missing; that is reported and the run carries on. `factory project show` lists every capability with its state: `available`, `missing` (with the reason), `not built` (the script is not there yet), `partial` (a `git` push where some repos have no `origin`), or `check_in_session` (MCP tools and skills, which only the Claude Code session can see).

Credentials never go in project config. An adapter names them in `requires.env`, and the core reads them from the environment or from the credential files in `requires.files` (only the names listed). They reach the script's environment and nothing else: the core redacts them from error lines and never prints the environment.

### Writing an adapter

A new adapter needs no code changes. This walk-through adds a tracker for a made-up service, Acme Tracker, that the Claude Code session reaches through an MCP server called `acme`.

1. Write the skeleton. It goes into `~/.factory/catalogue/adapters/acme-tracker/` (`--dir` puts it into another catalogue folder, such as a team's):

   ```sh
   factory adapter new acme-tracker --slot tracker
   ```

   A tracker skeleton reads issues through MCP (`fetch_issue`) and mirrors them in the central issue list (`import`, done by the core). A `code_host` skeleton pushes with git and opens PRs through MCP; a `design` skeleton fetches frames through MCP.

2. Fill in `adapter.json`. Every `<fill in: ...>` is a placeholder that `factory catalogue check` reports until it is gone:
   - `requires.mcp` of `fetch_issue`: the tool name as the session sees it, as a glob: `mcp__acme__get_issue`, or `mcp__*acme*__get_issue` when the server name varies.
   - `patterns`: `key_pattern` for keys (`^ACME-[0-9]+$`) and `url_pattern` for links, with a named group for the key (`^https://acme\.example\.com/issue/(?P<key>ACME-[0-9]+)$`). In JSON each backslash is written twice (`acme\\.example`). Use `[0-9]`, not `\d`; a pattern must not match the empty string, and keys may only hold letters, digits, `-` and `_`, because they end up in branch names.
   - `settings`: the keys a project may set, for example `{"workspace": {"type": "string", "default": null}}`. A project key that is not listed here is refused.
   - `vendors`: the vendor and product words of the service (`["acme"]`), so the neutrality test keeps them inside this folder when the adapter ships with the core.
   - Optional script capabilities, such as `sync`, run a command in the adapter folder: `{"via": "script", "command": ["python3", "scripts/sync.py", "--keys", "{external_keys}"]}`. The script prints one JSON object (the shapes are in spec 07, Script protocol) and exits 0, 3 for missing credentials, or anything else for "unknown, change nothing". Until the file exists the capability shows as `not built`.

3. Fill in `adapter.md`: `## Setup` (what the user connects, and how to check it), one `## <capability>` section per capability the orchestrator does, with the exact tool calls, what to save where and what never to print, and `## Never`.

4. Check it:

   ```sh
   factory catalogue check
   ```

5. Use it in a project, and look at what the project sees:

   ```sh
   factory project set shop integrations.tracker '{"adapter": "acme-tracker", "workspace": "shop"}'
   factory project show shop
   ```

   `fetch_issue` shows as `check_in_session`: at the start of a run the skill looks for the MCP tool with ToolSearch, and records a skip when it is missing.

6. Start a run from an issue in Claude Code: `/factory ACME-12`. The orchestrator follows the `## fetch_issue` section to read the issue, creates the run with `--external-key ACME-12` (which mirrors the issue in the central list and puts the key in the branch name), and writes `issue.md` from what it read.

## Token usage

`factory usage sync` reads the transcripts Claude Code writes anyway (`~/.claude/projects/`, or `$CLAUDE_CONFIG_DIR/projects/`) for the sessions tied to a run, and counts each assistant message once, per run, job, node, round and model. Subagents are told apart by the `key: value` lines of their first prompt. `factory usage show <run-id>` adds an estimated cost from a built-in price table, which carries the date its prices were checked; `~/.factory/prices.json` replaces it:

```json
{"date": "2026-11-01", "models": {"claude-opus-5-5": {"input": 4, "output": 20, "cache_read": 0.2}}}
```

Prices are dollars per million tokens. Cache writes cost the input price times 1.25 (5 minutes) or 2 (1 hour). A model the table does not know counts its tokens and shows its cost as unknown.

## Code layout

| Path | What |
|---|---|
| `.claude-plugin/` | The plugin manifest and the one-plugin marketplace. |
| `skills/factory/SKILL.md`, `agents/` | The orchestrator skill and the five agents, as `factory-spec/prompts/` has them. |
| `bin/factory` | The CLI entry point. |
| `install.sh` | The terminal install. |
| `factory/cli.py` | Argument parsing, JSON output, exit codes. |
| `factory/home.py`, `factory/doctor.py` | The factory home, `config.json`, `doctor`. |
| `factory/db.py`, `factory/schema.sql` | Connection settings, migrations, transactions, events. |
| `factory/projects.py` | Project config, resolution, the resolved snapshot. |
| `factory/adapters.py` | Adapter folders: validation, settings, patterns, availability, the script protocol, `adapter new`. |
| `factory/catalogue.py` | Step files, pipeline templates, the five-level lookup. |
| `factory/issues.py` | The central issue tracker and mirrors. |
| `factory/runs.py` | Runs, run repos, skips and free events. |
| `factory/plans.py` | Reading and validating `plan.md`. It writes nothing. |
| `factory/jobs.py` | Plan load, the job and group state machine, step and gate sync. |
| `factory/questions.py` | The question format and the question threads. |
| `factory/designs.py` | Design links, frame file names, the PNG and scrub check. |
| `factory/retro.py`, `factory/improvements.py` | `retro load`, improvement decisions, Improve groups and their PRs. |
| `factory/usage.py` | Transcript reading, the price table, costs. |
| `factory/skills.py` | Skill and agent copies, plugin folders, `factory agents`. |
| `factory/server.py`, `factory/dashboard.html` | The dashboard: routes, guards, the PR poller, and the page. |
| `factory/demo.py` | `factory demo`. |
| `factory/heads.py`, `factory/util.py` | File heads and small helpers. |
| `catalogue/` | The built-in steps, pipeline templates and adapters, as the spec ships them. |
| `examples/` | Example project and global configs. |

Only the modules above write the database, and only through `db.tx`. Migrations are listed in `db.MIGRATIONS`: `schema.sql` is version 1, and a later version is a new file appended to the list. Connecting migrates the database inside one transaction.

## Tests

```sh
python3 -m unittest discover -s tests
```

The tests use a temporary factory home and home folder per case, local git repos with a bare `origin`, and files written by the test in place of agents. They never call Claude, the network or a code host. Tests that need a vendor's name (an adapter's own scripts) live in that adapter's folder, `catalogue/adapters/<name>/tests/`, and fake the vendor's command line tool with a script on PATH; `tests/test_adapter_suites.py` loads them into the suite.

`tests/test_neutrality.py` keeps vendor and product names out of the core. It reads every file git does not ignore, tracked or not, and runs two checks, matching whole words regardless of case (an entry ending in `-` or `_` matches as a prefix):

- `[vendors]` always runs. Each adapter lists its vendor and product words in `vendors` in its `adapter.json`. Such a word may appear only in the folders of the adapters that list it, in `examples/` and in `factory-spec/`.
- `[never]` runs only when `tests/denylist.local.txt` exists. That file is gitignored, so no list of names is ever committed. Put the names that may appear nowhere under a `[never]` line, one per line; `#` lines are comments:

  ```text
  [never]
  Some Company
  some-product-
  ```

  Without the file the check is skipped with a message.

A failure names the file, the line and the word.

Two more tests keep the prompts honest. `tests/test_prompts.py` runs every CLI call that the skill, the agents and the adapters name through the CLI's argument parser, checks the JSON fields the skill reads against real command output, and checks that the prompt keys of each agent are the same in the skill, the agent file and spec 05. `tests/test_shell.py` parses every fenced `sh` block in the skill, the agents and the adapters with `sh -n`, `bash -n` and, when it is installed, `zsh -n`, after replacing each `<placeholder>` with a plain word.

## License

MIT, see `LICENSE`.
