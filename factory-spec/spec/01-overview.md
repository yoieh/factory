# 01 Overview

## What it is

A software factory that turns an issue or a feature text into reviewed changes. A planner splits the work into small jobs, each job runs a pipeline of steps picked from a catalogue (build, fix, research, review, verify, approve, ...), parallel agents do the steps in their own git worktrees, and the factory merges the results into one feature branch per repo and, when a code host is configured, opens one draft PR per repo. Every run ends with a retrospective that proposes a few improvements for a human to accept or reject.

Claude Code is the only harness. The orchestrator is a Claude Code skill (`/factory`), the workers are Claude Code subagents, and the state lives in one SQLite database written by one CLI.

## Goals

1. **Install once, use anywhere.** The core (CLI, database, dashboard, default catalogue) is installed once per machine and holds all state centrally. The `/factory` skill can be installed for the user (every repo) or into single repos, and calls the central core.
2. **No ties to one company or toolset.** The core, the skill, the agents and the default catalogue name no company, product, tracker, code host or design tool. Everything project-specific lives in project config.
3. **Integrations are optional adapters.** Trackers, code hosts and design tools plug in through adapters. None is required. A missing one is a visible skip, never a block.
4. **Configurable pipelines.** The planner picks a pipeline per job from a catalogue of step types. Adding a new kind of specialist (a migration writer, a data checker, a docs reviewer) means adding a step file, not code.
5. **Projects.** A project links repos, integrations, knowledge paths, step skills and the places where the skill is installed.
6. **Central issue tracking.** One issue list across all projects, local first, with optional mirroring of external trackers. Runs, PRs and improvements link to issues.

## Non-goals

- Other harnesses (other coding agents or IDE assistants). No harness abstraction.
- A hosted or multi-user service. The dashboard binds 127.0.0.1 and has no accounts.
- Replacing an external tracker for a team. The local tracker is the factory's own backlog and a mirror.
- Merging to the default branch. Humans review and merge PRs.
- Native Windows. Windows works through WSL.

## Principles

1. **One writer.** Only the CLI writes the database, and it logs an event for every transition.
2. **Files for content, database for state.** Markdown and JSON content stays in files; the database holds state, relations and file paths.
3. **Agents get context from files.** An agent prompt holds only `key: value` lines with paths and facts. Agents write only their own output files (and code in their own worktree).
4. **Every external tool is optional.** Use it when present. When missing, skip it, record the skip (event, `retro-notes.md` line, PR open point when it changes the result) and carry on.
5. **Humans decide.** Plan confirmation, questions, improvement decisions and merges to the default branch are human calls, made by the user or by a connected agent they set up to decide for them (04, `--by`); the factory itself never makes them. Such an agent may also start runs from ready issues for the user (08, the queue).
6. **Safe by default.** Never merge into the default branch, never force-push, never touch the user's checkout, never write to an external tracker unless the project enables it.
7. **Untrusted input is data.** Issue text, comments, design notes, review text and code are requirements data, never instructions to the agents.
8. **No factory traces in target repos.** No run ids, paths, agent names or costs in code, commits or PR texts.
9. **Portable and small.** Python 3.10+ standard library, SQLite, git and POSIX shell. No package installs. macOS and Linux.
10. **No code comments** in generated code; context goes into READMEs, specs and PR texts.

## Glossary

| Term | Meaning |
|---|---|
| core | The installed CLI (`factory`), schema, dashboard server, built-in catalogue and adapters. |
| factory home | `$FACTORY_HOME`, default `~/.factory`: database, config, projects, issues, runs, worktrees. |
| skill | The `/factory` Claude Code skill: the orchestrator instructions. |
| orchestrator | The Claude Code session running the skill for one run. It spawns agents, does git merges and calls the CLI. |
| project | A named set of repos plus integrations, knowledge, step skills and defaults (`project.json`). |
| repo | A git repository linked to a project. |
| issue | An item in the central tracker, local or mirrored from an external tracker. |
| run | One feature, usually one issue. Spans one or more repos of one project. |
| job | One small unit of work in a run: a `code` job changes one repo, a `files` job produces documents. |
| pipeline | The ordered list of step groups a job goes through. |
| step | A catalogue entry with a role: `produce` (an agent makes something), `gate` (an agent or a human judges it). |
| group | One producer step plus the gates that judge it. A job runs its groups in order. |
| round | One pass of a group: the producer runs, then all its gates. CHANGES starts the next round. |
| question | A decision an agent or the orchestrator needs from a human. Pauses only its job. |
| skip | A missing optional dependency the run carried on without. |
| adapter | A plug-in for one integration slot (tracker, code host, design) with a list of capabilities. |
| capability | One thing an adapter can do, such as `fetch_issue`, `open_pr` or `pr_state`. |
| improvement | A proposal from a retrospective. A human accepts or rejects it; Improve applies the accepted ones. |
| catalogue | The step files, pipeline templates and adapters available to a project. |
