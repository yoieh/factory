# 02 Architecture

## Pieces

```
                 ┌──────────────────────────── one machine ────────────────────────────┐
                 │                                                                     │
  any repo  ──►  │  /factory skill (user or repo scope)  ──calls──►  factory CLI       │
  (Claude Code)  │        │ spawns                                     │ only writer  │
                 │        ▼                                            ▼              │
                 │  subagents: planner, worker, reviewer,       $FACTORY_HOME         │
                 │  retro, improver (generic, driven by          factory.db (SQLite)  │
                 │  step files from the catalogue)               config.json          │
                 │                                               projects/ issues/    │
                 │  dashboard: factory serve  ◄──reads/answers── runs/ worktrees/     │
                 │  (127.0.0.1)                                  catalogue/           │
                 └─────────────────────────────────────────────────────────────────────┘
```

| Piece | Where | Role |
|---|---|---|
| Core | The plugin folder, and `$FACTORY_HOME/core/<version>/` with a `current` symlink | `bin/factory` (CLI), `factory/` (Python package), `schema.sql`, `serve.py` + `dashboard.html`, built-in `catalogue/` and `adapters/`, `skills/` and `agents/` templates, tests. |
| CLI | `factory` on the Bash tool's PATH from the plugin's `bin/`; in a terminal, a shim to `core/current/bin/factory` | The only writer of `factory.db`. Every command prints JSON on success. |
| Skill | The plugin (`/factory`, canonical `/factory:factory`), or copies in `~/.claude/skills/factory/` (user) and `<repo>/.claude/skills/factory/` (repo) | The orchestrator loop. Finds the CLI, never writes state itself. |
| Agents | The plugin (`factory:factory-planner`, ...), or copies in `~/.claude/agents/factory-*.md` and `<repo>/.claude/agents/` | Seven generic agents: `factory-planner`, `factory-worker`, `factory-reviewer`, `factory-retro`, `factory-improver`, `factory-lander`, `factory-watcher`. |
| Catalogue | built-in, then `$FACTORY_HOME/catalogue/`, then project dirs, then `<repo>/.factory/` | Step files, pipeline templates, adapters. Later entries override earlier ones by name. |
| Dashboard | `factory serve [--port 8765]` | Inbox, runs, projects, issues, improvements. Answers questions and records decisions through the same functions as the CLI. |

## Install

### Source layout

The core's own repo (the one `config.json` `factory_source` points at). Its root is also the root of the Claude Code plugin:

```
.claude-plugin/plugin.json        plugin manifest: name factory, version = the core version
.claude-plugin/marketplace.json   a one-plugin marketplace, so the repo can be added with /plugin marketplace add
install.sh
bin/factory
factory/            Python package (db, projects, catalogue, issues, runs, jobs, questions, adapters, designs, retro, usage, cli, server)
factory/schema.sql
factory/dashboard.html
skills/factory/SKILL.md     from prompts/skill/factory/SKILL.md of this spec
agents/factory-*.md         from prompts/agents/ of this spec
catalogue/steps|pipelines|adapters/
examples/                   from examples/ of this spec
tests/
README.md
```

Improvements with target `factory` point at these paths in `where`.

### Plugin

The primary install. In Claude Code:

```text
/plugin marketplace add <git url or path of the core's repo>
/plugin install factory@<marketplace>
```

- Claude Code puts the plugin's `bin/` on the Bash tool's PATH, after the user's own entries, and loads `skills/factory/` and `agents/`. Plugin components are namespaced: the skill is `/factory:factory` (the bare `/factory` works while no other command uses that name), and the agents are `factory:factory-planner` and so on.
- Any command creates a missing factory home, as `init` does, so the plugin needs no setup step.
- When the CLI runs from a plugin folder (one with `.claude-plugin/plugin.json`), it copies itself to `$FACTORY_HOME/core/<version>/` when that folder is missing, and points `current` at its version. The terminal shim and the dashboard then run the same core as the plugin.
- State never lives in the plugin folder: the folder changes with every plugin version, and Claude Code deletes plugin data on uninstall.
- `plugin.json` `version` equals the core version.

### Core in a terminal, or without the plugin

```sh
sh install.sh                    # copies core to $FACTORY_HOME/core/<version>/, links current, writes ~/.local/bin/factory
factory init                     # creates $FACTORY_HOME, config.json and factory.db; idempotent
factory doctor                   # checks python, git, sqlite, PATH, home permissions; prints fixes
```

- `install.sh` is POSIX sh. It never needs root. `--prefix <dir>` changes where the shim goes.
- With the plugin, `install.sh` only adds the terminal command (`factory serve`, `factory doctor`). Run it from the plugin folder; `factory doctor` prints that command when the shim is missing or runs another version.
- A git checkout of the core can be used directly: `FACTORY_CORE=<checkout>`; the shim then runs the checkout. This is how the factory improves itself.
- Upgrades install a new `<version>` folder and move `current`. The database migrates on connect (see 03).

### Skill and agents as copies

Copies are the alternative to the plugin: for a repo owner who wants the skill committed in the repo, or a machine without the plugin.

```sh
factory skill install --user                       # ~/.claude/skills/factory + ~/.claude/agents/factory-*.md
factory skill install --repo <path> [--project p]   # <repo>/.claude/skills/factory + agents, recorded on the project
factory skill plugin <dir>                          # writes a Claude Code plugin folder (skills/, agents/, .claude-plugin/plugin.json)
factory skill status                                # every recorded install, its version and drift from core
factory skill uninstall --user | --repo <path>
```

- Install copies the files (no symlinks, so a repo install can be committed by the repo owner if they choose). Each install is recorded in the `installs` table with scope, path, project and core version.
- Agents installed through a plugin get a namespaced `subagent_type` (`<plugin>:factory-worker`). `factory agents` prints the `subagent_type` per role for the current session's install, and the skill uses those names. When copies and the plugin both exist, repo scope wins, then user scope, then the plugin.
- Custom agents named by project step files are installed by the same command when their file is in the catalogue (`agents/` next to `steps/`).

### How the skill finds the core

The skill's Setup sets, in every Bash call (shell state does not persist):

```sh
F="${FACTORY_BIN:-$(command -v "${CLAUDE_PLUGIN_ROOT}/bin/factory" || command -v factory || printf '%s' "${FACTORY_HOME:-$HOME/.factory}/core/current/bin/factory")}"
```

`$F` is one word, so it works in bash and zsh. In the plugin, Claude Code fills in `${CLAUDE_PLUGIN_ROOT}` when it loads the skill, so the plugin's own CLI comes first, even when an older shim is on PATH. In a copied skill the variable is empty in the shell, and the line falls back to PATH, then to `core/current`. When `$F` does not run, the skill stops and tells the user to install the plugin or the core.

## Factory home

```
$FACTORY_HOME/                      default ~/.factory
  config.json                       global settings (see below)
  factory.db                        SQLite, WAL mode, busy_timeout 5000
  core/<version>/ core/current      installed core
  catalogue/steps|pipelines|adapters|agents/   user-level catalogue additions and overrides
  prices.json                       optional override of the model price table
  projects/<name>/project.json      project config (source of truth for project settings)
  issues/<KEY>-<n>.md               issue bodies
  runs/<run-id>/                    run files (see 05)
  improve/<batch>/<repo>/           improve batches
  worktrees/<run-id>/_feature/<repo>/   feature worktrees
  worktrees/<run-id>/<job-id>/          job worktrees
  worktrees/improve/<batch>/<repo>/
```

`config.json`:

```json
{
  "default_project": null,
  "dashboard": {"port": 8765, "hosts": []},
  "factory_source": null,
  "factory_project": null,
  "catalogue_dirs": [],
  "parallel": 3,
  "max_rounds": 4
}
```

- `factory_source`: path to a git checkout of the core. Improvements with target `factory` are applied there. When null, `factory` improvements are kept as issues only.
- `factory_project`: the project whose issue list holds accepted `factory` improvements. When null, they go to the run's own project.
- Unknown keys are an error.

## Project resolution

Every command that needs a project, and the skill, resolve it in this order, first match wins:

1. `--project <name>`
2. `FACTORY_PROJECT`
3. `<git toplevel of cwd>/.factory/project`: a one-line file with the project name
4. a project whose `repos[].path` equals the git toplevel of cwd, or whose `repos[].remote` equals its `origin` URL (normalised: no `.git`, no credentials, ssh and https forms equal)
5. `config.json` `default_project`
6. none: an **ad-hoc project**: `run new` registers a project named after the repo (`<repo-name>`, with `-2` and so on on a clash), a key from its letters (`^[A-Z][A-Z0-9]{1,9}$`, unique), one repo (the git toplevel of cwd), no integrations and no knowledge, with the normal catalogue lookup. It is recorded as a skip ("project", "ad-hoc project from the current repo"). From then on step 4 finds it like any other project.

When step 4 matches more than one project, the command fails and asks for `--project`. A run never starts in a repo that is not in its project (ad-hoc included).

## Access

The orchestrator session needs read and write access to every repo of the project and to `$FACTORY_HOME`. `factory doctor --project <name>` prints the exact start command:

```sh
claude --add-dir <repo-1> --add-dir <repo-2> --add-dir ~/.factory
```

When a read or write is refused, the skill stops and shows that command.

## Concurrency

Several orchestrator sessions can run at once, one run each. SQLite in WAL mode with `busy_timeout` 5000 ms. One dashboard server per machine. Each worktree belongs to one run; nothing is shared between runs except the user's repos, which are only used through `git -C <repo> fetch` and `worktree add`.

## Platform rules

- Python 3.10+ standard library only. No third-party packages, no build step.
- Shell snippets in the skill and agents must work in bash and zsh: quote every variable, no arrays, no `set --` word splitting, no `local`, no process substitution, write every argument out in full.
- Paths: expand `~`, resolve to absolute paths before storing. Never store relative paths in the database.
- Time: UTC ISO 8601 with `Z`.
