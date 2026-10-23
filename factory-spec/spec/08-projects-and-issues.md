# 08 Projects and issues

## Project config

`$FACTORY_HOME/projects/<name>/project.json` is the source of truth for a project's settings. The CLI writes it (`project add`, `project link-repo`, `project set`) and validates it on every read; a human may edit it by hand, and the next command validates it again. The `projects` table only indexes it.

```json
{
  "name": "shop",
  "key": "SHOP",
  "description": "Web shop and its services",
  "repos": [
    {"name": "api", "path": "~/src/shop/api", "remote": "git@example.com:shop/api.git",
     "default_branch": "main", "role": "service", "test": "make test", "setup": "make deps"},
    {"name": "web", "path": "~/src/shop/web", "default_branch": "main", "role": "frontend"}
  ],
  "repo_root": "~/src/shop",
  "integrations": {
    "tracker": {"adapter": "local"},
    "code_host": {"adapter": "github"},
    "design": {"adapter": "figma"}
  },
  "knowledge": {
    "label": "shop docs",
    "read_first": ["~/src/shop/docs/architecture.md"],
    "per_repo": ["~/src/shop/docs/services/{repo}.md"],
    "architecture": ["~/src/shop/docs/principles.md"]
  },
  "step_skills": {
    "code": [{"path": "~/src/shop/docs/python-standards.md", "when": "Python repos"}],
    "ui":   [{"path": "~/src/shop/docs/brand-colors.json", "when": "brand colors"}]
  },
  "catalogue_dirs": ["~/src/shop/factory-steps"],
  "improvement_targets": {
    "factory":   {"label": "factory"},
    "repo":      {"base": "default", "label": "repo"},
    "knowledge": {"repo": "docs", "base": "default", "label": "shop docs"},
    "process":   {"label": "issues and process"}
  },
  "defaults": {"parallel": 3, "max_rounds": 4, "confirm_plan": false, "branch_prefix": "feature/",
               "require_code_review": true, "land": "off"}
}
```

Keys:

| Key | Required | Meaning |
|---|---|---|
| `name` | yes | `^[a-z][a-z0-9-]{1,39}$`, equals the folder name. |
| `key` | yes | Issue key prefix, `^[A-Z][A-Z0-9]{1,9}$`, unique across projects. |
| `description` | no | One line. |
| `repos` | yes (may be empty) | `name` (`^[A-Za-z0-9][A-Za-z0-9._-]*$`, unique), `path` (a git checkout), optional `remote`, `default_branch` (read from `origin/HEAD` when missing), `role` (free text for the planner), `test` and `setup` (hints for the planner; the plan's repo facts win), `after_merge` (below). |
| `repo_root` | no | A folder the planner may scan for repos not yet linked; it may propose linking one in a question, never use it unlinked. |
| `integrations` | no | See 07. |
| `knowledge` | no | Paths the agents read when they exist: `label`, `read_first`, `per_repo` (with `{repo}`), `architecture`. |
| `step_skills` | no | Per `skills_key` of a step, a list of `{path, when}` files the step's agent reads. |
| `catalogue_dirs` | no | Extra catalogue folders (06). |
| `improvement_targets` | no | Where accepted improvements go. Default: `factory`, `repo`, `process`; `knowledge` only when set. Per target: `repo` (a project repo, or none for `factory` and `process`), `base` (`current`, `default` or a branch name), `label`. |
| `defaults` | no | Run defaults. `land` (`off`, `ready` or `merge`, default `off`) says what `land apply` does with a PR the lander passed: nothing, mark it ready for review, or merge it. Only the user turns it on, per project. |

A repo's `after_merge` says what the factory does once its PR is merged, in **After merge** of Land. Only the user sets it.

| Key | Meaning |
|---|---|
| `watch` | `true`: a `factory-watcher` follows the merge commit's deploy with the `deploy-watch` skill, rolls back a production outage the deploy caused, and opens (never merges) a remediation PR. |
| `build` | A shell command, run with the repo's `setup` in a detached worktree at the merge commit. |
| `artifact` | The build's output: a path inside the repo, or a list of them (for example one zip per platform), each with its own file name. A folder is zipped. Needs `build`. |
| `release` | `off` (default), `draft` or `publish`: upload the artifacts together as one release of the code host. Needs `artifact`. |
| `package` | `off` (default) or `publish`: push the artifacts as one package of the repo. Needs `artifact`. |

`factory project set <name> repos.<repo>.after_merge '<json>'` sets it for one repo.

Validation: unknown keys at any level are an error, paths are expanded and must be absolute after expansion, repo paths must be git checkouts (a warning in `project show`, an error in `run new` for a repo the run uses). Credentials never go in project config; see 07 `settings`.

### Resolved snapshot

`project show --json` and `run new` (into `runs/<run-id>/project.json`) give the same resolved form: the config with absolute paths, per adapter its slot, the paths of `adapter.json` and `adapter.md`, and per capability `via`, `requires` (including `mcp` patterns and `skill` names) and availability, only the knowledge and skill paths that exist (`knowledge.per_repo` becomes an object of repo name to the expanded paths that exist), and `skipped` for everything left out. The resolved form always fills in the defaults: `improvement_targets` (`factory`, `repo`, `process`, plus `knowledge` when knowledge is set), every `defaults` key, and the default slots (tracker `local`, code host `none`). Every agent gets this file as `project`. The dashboard's `GET /api/projects/<name>` returns the same without paths that point outside the repos and the factory home, and never any settings value marked `secret` by an adapter.

## Commands

```sh
factory project add <name> --key <KEY> [--description "<text>"]
factory project link-repo <name> <path> [--as <repo-name>] [--role <role>] [--default-branch <b>]
factory project unlink-repo <name> <repo-name>
factory project set <name> <dotted.key> <json-value>        # e.g. integrations.code_host '{"adapter":"github"}'
factory project show [<name>] [--json]                       # resolved, with availability and skips
factory project list
factory project here                                         # which project the cwd resolves to, and why
factory project pin <name> [--repo <path>]                   # writes <repo>/.factory/project
factory project archive <name>
```

`project here` prints the resolution step that matched (02, Project resolution). `project pin` writes the one-line pointer file; committing it is the repo owner's choice.

## Where the skill is installed

`factory skill install --repo <path> --project <name>` records the install on the project. `project show` lists, per project, the repos with a repo-scope install and whether the user-scope install exists, with the version of each and drift from the core. `factory skill status` lists all installs.

## Central issue tracker

The local tracker is the default tracker of every project. It holds issues across all projects in one table; bodies live in `$FACTORY_HOME/issues/<KEY>-<n>.md`.

### Commands

```sh
factory issue add <project> "<title>" [--type feature|bug|task|research|improvement] [--priority high|medium|low]
                  [--label <l>]... [--body-file <path> | --body "<text>"]
factory issue list [--project p] [--status s]... [--type t] [--label l] [--json]
factory issue show <KEY-n> [--json]
factory issue set <KEY-n> [--status s] [--priority p] [--title "<t>"] [--label <l>]... [--unlabel <l>]...
factory issue edit <KEY-n> --body-file <path>
factory issue link <KEY-n> --run <run-id> | --pr <url> | --issue <KEY-m> | --improvement <id>
factory issue import <project> <external-key> --title "<t>" [--url <url>] [--external-status s] [--body-file <path>]
factory issue sync <project>                     # tracker adapter's sync script, mirrored issues only
```

- Keys: `<project key>-<n>`, `n` counting per project from 1.
- `issue import` creates a mirror with `source` = the tracker adapter's name and `external_key`. A second import of the same external key updates the mirror. Mirrors are read-only copies: `issue set --status` on a mirror changes only the local status and says so; it never writes to the external tracker unless the project tracker has `"write": true` and the orchestrator does it through the adapter.
- Starting a run: `/factory SHOP-12` resolves a local key first. `run new --issue SHOP-12` writes `issue.md` from the body, links the run (`issue_links`), and sets the issue `in_progress`. An external key or URL goes through the tracker adapter (07): the orchestrator fetches it, then `run new --external-key <K> [--external-url <U>]` creates or updates the mirror and links the run, so the run always links to a local key, while the branch name carries the external key so the external tracker links branches and PRs by itself.
- Automatic status moves (03): `in_progress` on run start, `in_review` when the first PR is stored, `done` when every PR of the issue's latest run is merged. A failed run leaves the issue `in_progress` and adds an event.
- Improvements: when a human accepts an improvement, the core creates an issue of type `improvement` in the run's project (or, for target `factory`, in the project named by `config.json` `factory_project`, else the run's project) and links both ways. The issue moves to `done` when the improvement does. Rejecting closes it.
- `/factory` with no arguments in a repo lists the project's `ready` issues and offers to start one.

### Dashboard

An Issues page lists issues across projects with filters (project, status, type, label, text), shows one issue with its body (rendered markdown), links (runs, PRs, improvements) and history, and lets a human create an issue, change its status, priority, labels and title, and edit its body. Starting a run stays in Claude Code: the page shows the command to copy (`/factory SHOP-12`).
