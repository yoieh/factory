# 07 Integrations

Integrations plug in through adapters. A project names at most one adapter per slot. No slot is required.

## Slots and capabilities

| Slot | Capability | What it does |
|---|---|---|
| tracker | `fetch_issue` | Read one issue with its comments, links, attachments list and design links, to write `issue.md`. |
| tracker | `import` | Mirror an external issue into the central tracker by hand (`issue import`). Runs mirror through `run new --external-key`. |
| tracker | `sync` | Refresh mirrored issues (title, URL, external status) in bulk. Optional. |
| tracker | `comment` | Post one comment on the external issue. Off unless the project sets `"write": true`. |
| code_host | `push` | Push the feature branch to `origin`. |
| code_host | `open_pr` | Open or update a draft PR from the feature branch to the base branch, with the reviewers the host adds by itself (default reviewers, code owners) or a `reviewers` setting. |
| code_host | `pr_state` | Read a PR's state: `OPEN`, `MERGED`, `DECLINED`, `SUPERSEDED`. Used by `run prs`, `improvement prs --check` and the dashboard poller. |
| code_host | `pr_template` | A file or a skill that defines the PR title format and sections. |
| design | `fetch_design` | Save one PNG and one markdown file per frame into `design/` (05, Design files). |

Recognising keys and links, and scrubbing temporary URLs, are not capabilities: they are `patterns` in `adapter.json`, used by the core.

Each capability has a `via` that says who does it and how:

| `via` | Done by | How |
|---|---|---|
| `mcp` | orchestrator | An MCP tool, following `adapter.md`. |
| `cli` | orchestrator | A binary on PATH, following `adapter.md`. |
| `skill` | orchestrator | A Claude Code skill loaded by name, following `adapter.md`. |
| `git` | orchestrator | Plain git (`push`). |
| `file` | orchestrator | A file in the adapter folder (`pr_template`). |
| `script` | core, or the orchestrator through `factory adapter run` | A command in `adapter.json` (script protocol below). |
| `core` | core | Built into the CLI (the `local` tracker, `import`). |

The dashboard server cannot call MCP tools, so anything it needs (`pr_state`) must be a `script`.

## Adapter folder

```
adapters/<name>/
  adapter.json        what it is and what it can do
  adapter.md          instructions for the orchestrator: Setup, one section per orchestrator capability, Never, Scripts to build
  scripts/            optional; Python stdlib or POSIX sh
  pr-template.md      optional
```

`adapter.json`:

```json
{
  "name": "github",
  "slot": "code_host",
  "version": 1,
  "vendors": ["github", "gh"],
  "capabilities": {
    "push": {"via": "git"},
    "open_pr": {"via": "cli", "requires": {"bin": ["gh"]}},
    "pr_state": {"via": "script", "command": ["python3", "scripts/pr_state.py", "--url", "{pr_url}"],
                 "requires": {"bin": ["gh"]}},
    "pr_template": {"via": "file", "path": "pr-template.md"}
  },
  "settings": {"draft": {"type": "bool", "default": true},
               "reviewers": {"type": "list", "default": null}},
  "patterns": {"pr_url": "^https://github\\.com/[^/]+/[^/]+/pull/[0-9]+$"}
}
```

- `vendors`: the names of the tool's vendor and products, lowercase (`^[a-z0-9][a-z0-9_-]*$`), possibly empty. Not used at run time: the neutrality test (13) allows each of these words only in the folders of the adapters that list it, in `examples/` and in `factory-spec/`.
- `requires`:
  - `bin`: binaries that must be on PATH.
  - `env`: variable names; set in the environment or found in `files`.
  - `files`: credential files, `~` allowed, each a list of `KEY=value` lines; an optional leading `export `, optional single or double quotes around the value, `#` comment lines and blank lines ignored. Only the names in `env` are read.
  - `mcp`: tool name globs (`fnmatch`, case-sensitive) matched against the full tool name as the session sees it, for example `mcp__*__getJiraIssue`. Name the server part when a tool name is common (`mcp__*figma*__get_metadata`).
  - `skill`: skill names.
- `settings`: the keys a project may set for this adapter. Types: `string`, `int`, `bool`, `list` (of strings); `default` may be null. A project key not listed here is an error, so a token pasted into project config by mistake is refused, not saved. Credentials always come from `env` or `files`, never from settings.
- `patterns`: `key_pattern` and `url_pattern` (tracker), `pr_url` (code host), `design_url` and `scrub` (a list, design). Each must compile and must not match the empty string. Use `[0-9]`, not `\d` (which matches non-ASCII digits).

## Keys

- A key is extracted from an argument by `key_pattern` (the whole match) or by `url_pattern`, which must hold a named group `(?P<key>...)`; the key is that group. A key the tracker patterns extract must also match `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`, checked on the extracted key, because keys land in branch names. A key that fails is refused with a clear message.
- Some trackers have no key prefix (numbered issues): the key is the number, and the branch is `<branch_prefix><number>-<slug>`.
- A local key and an external key can have the same shape. Resolution (09): a key that exists in the local tracker is local. When the project's tracker is not `local` and the key also matches that tracker's `key_pattern`, the skill asks one question: local issue or external issue. With the `local` tracker no question is asked. For a tracker other than `local`, `project add` and `project set` warn when the project key equals a prefix the tracker's `key_pattern` accepts, and suggests a different local key.

## Script protocol

- The core runs `command` with `{placeholders}` filled in, cwd = the adapter folder, a 30 second timeout, and the environment plus the `env` names read from `files`.
- Placeholders:

  | Placeholder | Value |
  |---|---|
  | `{<setting>}` | the project's value of that setting, or its default (`list` as a comma list, `bool` as `true`/`false`) |
  | `{project}` | the project name |
  | `{repo}`, `{repo_path}`, `{remote_url}` | the repo of the call; empty for project-level calls (`sync`) |
  | `{source_branch}`, `{target_branch}`, `{pr_file}`, `{title}` | `open_pr` inputs |
  | `{pr_url}` | `pr_state` input |
  | `{issue_key}`, `{external_key}`, `{external_keys}` | issue inputs; `external_keys` is a comma list of the project's mirrored keys for `sync` |

  Setting names must not equal a built-in placeholder. A placeholder without a value is an error before the script runs.
- stdout is one JSON object:

  | Capability | Output |
  |---|---|
  | `pr_state` | `{"state": "OPEN"\|"MERGED"\|"DECLINED"\|"SUPERSEDED", "url": "..."}` |
  | `open_pr` | `{"action": "created"\|"updated", "url": "..."}` |
  | `sync` | `{"issues": [{"external_key", "title", "url", "external_status", "updated_at"}]}` |
  | `fetch_issue` | `{"title", "url", "status", "assignee", "type", "labels", "parent", "description", "comments", "links", "attachments", "design_links"}` |

- Exit code 0 is success. Exit 3 means "credentials missing" (the caller reports it and carries on). Any other non-zero exit, or bad JSON, is "unknown" and changes nothing.
- Secrets never reach stdout, logs, events or files. The core never prints the environment.
- `factory adapter run <name> <capability> [--project p] [--repo r] [--<placeholder> <value>]...` runs a script capability with the project's settings. `--repo` fills `{repo}`, `{repo_path}` and `{remote_url}` from project config; an explicit `--<placeholder>` maps one-to-one to `{placeholder}` and wins; flags the command does not use are ignored. For a checkout that is not a project repo (the factory source in Improve), pass `--repo_path` and `--remote_url` instead of `--repo`. It prints the script's JSON and passes exit code 3 through. The orchestrator uses it for script capabilities such as a script-based `open_pr`.

## Sync and mirrors

`sync` updates a mirror's title, URL and `external_status` (a column of its own, shown on the dashboard), never its local `status`. The local status follows the factory's own moves (03) and human edits. An adapter may offer a `status_map` setting that maps external statuses to a suggested local status, shown as a hint only.

## Availability

`factory project show <name>` reports per slot and capability whether it is available:

- `script`, `cli`: the binaries in `requires.bin` are on PATH and every name in `requires.env` is set or found in `requires.files`. For `script`, the files that `command` names inside the adapter folder must also exist; a script that is not built yet reports `not built`.
- `git`: the repo has an `origin` remote (per repo).
- `file`: the file exists.
- `core`: always.
- `mcp`, `skill`: the core cannot see the session's tools; it reports `check_in_session`. The orchestrator checks at the start of the run, `mcp` with ToolSearch and `skill` against the session's skill list, and records a skip when one is missing.

A missing capability is a skip, never a block:

| Missing | Effect |
|---|---|
| tracker or `fetch_issue` | Pasted issue text becomes the Description; with no text but a repo, a feature-text run with the key as reference; only with neither, one question. Skip with `pr: true` when the run builds from pasted text. |
| `push` for every repo of the run | `run new` sets `no_pr`; Finish stops after the full tests; Publish refuses; Improve commits but pushes nothing. |
| `push` for one repo | That repo gets `can_push: false`: Finish skips its push and PR with a skip; the other repos go on. |
| `open_pr` (push available) | Finish pushes the feature branch and stops there for that repo: the report says to open the PR by hand, with the branch name. Skip without `pr`. |
| `pr_state` | PR states are not read; the dashboard shows no state pill; Improve reports PRs as unchecked and carries on. |
| `pr_template` | The repo's own PR template when the host uses one (for example a template file in the repo), else the plain template (05). |
| `comment` or `"write": false` | Nothing is written to the tracker. Not a skip: it is the default. |
| design or `fetch_design` | Design links are listed as "not fetched"; skip with `pr: true` ("UI built without the design"). |

## Built-in adapters

v1 ships:

| Adapter | Slot | How |
|---|---|---|
| `none` | code_host | No push, no PR. The default. |
| `git` | code_host | `push` only; the PR is opened by hand. For hosts without a CLI or API. |
| `local` | tracker | The central issue tracker itself (`via: core`). Default tracker of every project. |
| `github` | code_host | `gh` CLI for `open_pr`, a script for `pr_state`. |
| `github-issues` | tracker | `gh issue view` for `fetch_issue`, a script for `sync`. Keys are issue numbers. |
| `figma` | design | Figma MCP: metadata, screenshot, design context per frame; `scrub` for asset URLs. |

`github` and `github-issues` come first: their scripts are built and tested in v1. `figma` is MCP only, so it has no script to build.

v1.1 examples, same interface. Their folders ship with `adapter.json` and `adapter.md`; their scripts are not built in v1, so those capabilities report `not built`.

| Adapter | Slot | How |
|---|---|---|
| `gitlab` | code_host | `glab` CLI. |
| `bitbucket` | code_host | Scripts for `open_pr` (adds default reviewers) and `pr_state`, with an API token from env or a credential file. |
| `jira` | tracker | MCP for `fetch_issue` and `comment`; optional REST script for `sync`. |
| `linear` | tracker | MCP for `fetch_issue`. |

## Writing an adapter

A new adapter, for example one built on an MCP server, is a folder and needs no code changes:

```sh
factory adapter new <name> --slot tracker|code_host|design [--dir <catalogue dir>]
```

- It writes `adapters/<name>/adapter.json` and `adapter.md` under `--dir`, by default `$FACTORY_HOME/catalogue/`, and refuses a folder that exists.
- `adapter.json` gets the slot's orchestrator capabilities `via: mcp` (tracker `fetch_issue`, plus `import` via `core`; code host `push` via `git` and `open_pr`; design `fetch_design`), `requires.mcp` placeholders to fill in, an empty `vendors` list and empty `settings` and `patterns`.
- `adapter.md` gets `## Setup`, one `## <capability>` section per orchestrator capability, and `## Never`.
- `factory catalogue check` validates adapters as well as steps and templates, and reports placeholders that are still unfilled.
- The core's README walks through an MCP-based tracker adapter from `adapter new` to a first run.

## Project config for integrations

```json
"integrations": {
  "tracker":   {"adapter": "jira", "site": "example.atlassian.net", "write": false},
  "code_host": {"adapter": "github", "draft": true},
  "design":    {"adapter": "figma"}
}
```

`adapter` names the adapter. The other keys must be in that adapter's `settings`, plus `write` (trackers) and `patterns` (an object that overrides any of the adapter's patterns, for example `pr_url` for a self-hosted instance; overrides pass the same pattern rules). Missing slots mean: tracker `local`, code host `none`, no design.

## The orchestrator's part

For each capability done by the orchestrator, `adapter.md` has a section `## <capability>` that says exactly which tool to call with which arguments, what to save where, and what never to print (short-lived signed URLs, tokens). The skill reads `adapter.md` of the project's adapters at the start of a run and follows it; it never calls a host's API another way. `adapter.md` also has `## Setup` (what the user installs or sets), `## Never` and, when the adapter names scripts, `## Scripts to build` (arguments, output, exit codes).

For a tracker, the run flow is always: `fetch_issue`, then `run new --external-key <key> [--external-url <url>]` (which creates or updates the mirror and links the run), then write `issue.md` from the fetch, then `factory issue edit <issue_key> --body-file <issue_file>` so the mirror holds the same text.
