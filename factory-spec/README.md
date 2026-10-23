# Software factory: spec and prompts

The spec, prompts and catalogue for a general software factory. Install it once per machine; it runs from any git repo with Claude Code, links repos into projects, keeps one central issue list, and lets a planner build a pipeline per job from a catalogue of specialist steps. Trackers, code hosts and design tools are optional adapters.

This folder is reverse-engineered from a working single-project factory and generalized. It contains no code: it is the brief for building the factory somewhere else.

## How to use

1. Create an empty git repo for the new factory and copy this folder into it as `factory-spec/`.
2. Start Claude Code in that repo.
3. Paste the prompt from `BUILD-PROMPT.md`. It works in phases and stops for your go after each one.

## Contents

| Path | What |
|---|---|
| `BUILD-PROMPT.md` | The prompt that starts the build, phase by phase. |
| `DECISIONS.md` | The decisions taken before and during the build, and where they changed the spec. |
| `spec/01-overview.md` | Goals, non-goals, principles, glossary. |
| `spec/02-architecture.md` | Pieces, install, factory home, project resolution, access, platform rules. |
| `spec/03-data-model.md` | The SQLite schema, migrations and state machines. |
| `spec/04-cli.md` | Every CLI command and what it returns. |
| `spec/05-file-contracts.md` | Run folder, prompt keys per agent, file heads, the question format, plan, spec, output, gate, retro and improve files. |
| `spec/06-pipelines.md` | Steps, groups, gates, human gates, templates, catalogue lookup, validation. |
| `spec/07-integrations.md` | Adapter slots and capabilities, adapter folders, the script protocol, availability and skips. |
| `spec/08-projects-and-issues.md` | Project config, project commands, skill installs, the central issue tracker. |
| `spec/09-orchestrator.md` | The run flow the skill follows, and the Never list. |
| `spec/10-dashboard.md` | Pages, API, security, background work. |
| `spec/11-usage-retro-improve.md` | Token usage, the retrospective, improvement targets, Improve. |
| `spec/12-reference-lessons.md` | What the reference factory learned in real runs. |
| `spec/13-tests-and-acceptance.md` | Test plan and acceptance criteria. |
| `spec/14-open-questions.md` | Decisions to take before building, with recommendations. |
| `prompts/skill/factory/SKILL.md` | The `/factory` orchestrator skill. |
| `prompts/agents/` | The five generic agents: planner, worker, reviewer, retro, improver. |
| `catalogue/steps/` | Built-in produce and gate steps. |
| `catalogue/pipelines/` | Built-in pipeline templates. |
| `catalogue/adapters/` | Built-in adapters (v1) and examples (v1.1). |
| `examples/` | Example project configs and a global config. |

## What carries over from the reference

The single writer CLI with events, the file contracts and heads, the strict question format with ask-back, worktree per job with squash merge into one feature branch per repo, WAIT FOR ALL with a round cap, visible skips for missing tools, token counting from transcripts, the evidence-based retrospective with human decisions, and the local dashboard with its inbox.

## What is new

Install once and call from any repo; projects that link repos; a central issue tracker; adapters for trackers, code hosts and design tools; pipelines composed from a step catalogue, with files jobs and human gates; five generic agents driven by step files instead of one agent per reviewer.
