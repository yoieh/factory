# 06 Pipelines and the step catalogue

The reference factory had one fixed loop: build, then the picked reviewers, then merge. Here the planner composes a pipeline per job from a catalogue. The fixed loop is the default template `code-change`, so the simple case works as before.

## Concepts

- A **step** is a catalogue file with a role:
  - `produce`: an agent makes something. In a `code` job it changes code on the job branch; in a `files` job it writes under `out/`.
  - `gate`: an agent, or a human, judges the latest producer's result and writes a verdict.
- A **group** is one producer step plus 0 to 6 gates. Groups run in order. Within a group: producer, then all gates in parallel (WAIT FOR ALL), then PASS moves to the next group, CHANGES starts the next round of the same producer.
- After the last group: a `code` job is squash-merged into its repo's feature branch by the orchestrator; a `files` job is delivered (its `out/` folder is listed in the run and handed to dependants as `upstream`).

## Step file

`steps/<name>.md`. The name is `^[a-z][a-z0-9-]{1,39}$` and equals the file name. `rework`, `output`, `reply` and `round` are reserved and cannot be step names.

```markdown
---
name: review-security
role: gate
kinds: [code]
agent: factory-reviewer
model: opus
summary: Checks auth, input handling, secrets and data access.
when: The job touches auth, input handling, secrets, data access or a public endpoint.
requires: []
skills_key: [security]
version: 1
---

## Focus
- ...

## Must fix
What counts as must-fix for this step. Everything else is a note.

## Read
Extra things to read beyond the generic agent's standard list (below).

## Rules
Step-specific rules.
```

Front matter keys:

| Key | Values | Default |
|---|---|---|
| `name` | the file name without `.md` | required |
| `role` | `produce` or `gate` | required |
| `kinds` | list of `code`, `files` | required |
| `agent` | an agent name, or `human` (gates only) | `factory-worker` for produce, `factory-reviewer` for gate |
| `model` | a model alias or id, passed as the Agent tool's `model` | the agent's own model |
| `summary` | one line | required |
| `when` | one or two sentences for the planner | required |
| `requires` | list of `designs`, `upstream`, `previous`, `repo-tests`, `issue` | `[]` |
| `gates_optional` | produce only: `true` when a group may have no gates | `false` |
| `code_review` | produce only: `required` or `optional`; with `required`, a code group of this step needs `review-code` when the project's `defaults.require_code_review` is true | `required` |
| `skills_key` | list of keys in `project.json` `step_skills` whose files this step's agent reads | `[]` |
| `version` | integer | `1` |

- The front matter is a small YAML subset, parsed by the core without a YAML library: one `key: value` per line, the value is the rest of the line after the first `: `, lists are `[a, b]`, booleans are `true` and `false`, integers are digits. No comments, no nesting, no multi-line values. Unknown keys are an error.
- The body is the step's instructions. The generic agent reads it as its `step_file` (producer) or `gate_file` (gate). Sections, in this order: producers `## Focus`, `## Read`, `## Rules`, `## Output` (what `output.md` or `out/` must hold beyond the standard template); gates `## Focus`, `## Must fix`, `## Read`, `## Rules`, and for human gates `## Ask` last.
- `when` is what the planner reads to decide whether the step fits a job.
- `requires`:
  - `designs`: only allowed when the run has a `design/` folder with files.
  - `upstream`: only allowed in a job with `depends_on`.
  - `previous`: only allowed in a group that is not the job's first (for example `fix` after `reproduce`).
  - `repo-tests`: the repo's facts in plan.md name a test command.
  - `issue`: the run has an `issue.md`.
- `agent: human` makes a human gate: no agent runs; the orchestrator asks the question in the body's `## Ask` section, and the answer becomes the verdict (see Human gates). `## Ask` is found as a line that is exactly `## Ask`; it must be the last section of the file and appear once. Its option A is the approve option.
- `skills_key` lets a project attach its own skill files to the step (`step_skills.<key>` in `project.json`), for example a language standards file for `build`, `fix` and `review-code` under the key `code`.

## What the generic agents always do

Step files only add to this list; they never repeat it.

- Worker (produce): read `spec.md` (its `## Notes for steps` section for this step), the repo's section of `plan.md`, `issue.md`, the repo's own rules (`CLAUDE.md`, `AGENTS.md`, `README*`, contributing and lint config), the `step_skills` files of the step's `skills_key` whose `when` fits, the `.md` and `.png` design files the spec lists, every `previous` group dir (its `output.md` and last gate round), every `upstream` job dir (its last `output.md` and the Notes of its last gate round), and in round 2 and later the previous round's gate files and `rework.md`. Code jobs work only in `worktree`; files jobs write only under `out_dir` and read code only through `sources`.
- Reviewer (gate): read the same inputs, the producer's `output.md`, the group diff (`git -C <worktree> diff <group_base>..<branch>`, with the full job diff `<feature_branch>...<branch>` for context only) for code jobs or the files under `out_dir` for files jobs, earlier rounds (never raise a settled point again), and check that `upstream` notes naming this job were handled. Read-only: it may run tests and read-only commands, never edit or commit.

## Pipeline template

`pipelines/<name>.json`:

```json
{
  "name": "code-change",
  "description": "A change in one repo: build, then review.",
  "kinds": ["code"],
  "when": "Most code changes.",
  "pipeline": [
    {"step": "build", "gates": ["review-code"], "optional_gates": ["review-security", "review-ux", "review-ui", "review-arch"]}
  ]
}
```

The planner starts from a template, then adds optional gates by their `when`, or composes its own pipeline from steps when no template fits. `plan load` validates the result, not the template choice.

## Built-in catalogue (v1)

Produce steps:

| Step | Kinds | Purpose |
|---|---|---|
| `build` | code | Implement the spec in the worktree, test, commit. |
| `reproduce` | code | Write a failing test that shows the bug; commit it; no fix. |
| `fix` | code | Make the reproducing test pass with the smallest change; run the suite. |
| `migrate` | code | A schema, data, config or dependency migration with rollback notes and a dry run. |
| `research` | files | Answer the spec's questions from code, docs and the issue; write `out/findings.md` with evidence. |
| `write-doc` | files, code | Write or update docs (`out/` for files jobs, the repo's docs for code jobs). |

Gate steps:

| Step | Kinds | Purpose |
|---|---|---|
| `review-code` | code | Correctness, tests, repo conventions. Default gate of every code group. |
| `review-security` | code | Auth, input handling, secrets, data access, dependencies. |
| `review-ux` | code | Flows, states, copy, accessibility; against the design when there is one. |
| `review-ui` | code | Layout, components, tokens, responsiveness; against the design when there is one. |
| `review-arch` | code | The project's architecture rules, data ownership, contracts. Needs `knowledge.architecture` or repo docs. |
| `review-docs` | code, files | Accuracy against the code, completeness, audience. |
| `review-findings` | files | Every claim has evidence, the spec's questions are answered, open points are named. |
| `verify` | code | Runs the app or the tests end to end and checks the acceptance criteria by behaviour, not by reading. |
| `approve` | code, files | A human gate: a person approves the result or asks for changes. |

Templates:

| Template | Pipeline |
|---|---|
| `code-change` | `build` [`review-code`] |
| `ui-change` | `build` [`review-code`, `review-ux`, `review-ui`] |
| `bug-fix` | `reproduce` [`review-code`] > `fix` [`review-code`, `verify`] |
| `migration` | `migrate` [`review-code`, `review-arch`, `approve`] |
| `research` | `research` [`review-findings`] |
| `docs` | `write-doc` [`review-docs`] |

## Lookup and override

The core builds the catalogue for a project from these folders, later ones win by name:

1. `core/current/catalogue/`
2. `$FACTORY_HOME/catalogue/`
3. each folder in `config.json` `catalogue_dirs`
4. each folder in the project's `catalogue_dirs`
5. `<repo>/.factory/` of each repo in the project (only `steps/` and `pipelines/`)

`factory catalogue show [--project p] --json` prints the merged catalogue: per step every front matter key (with defaults filled in), its absolute path and source folder; per template its content. `run new` writes this to `runs/<run-id>/catalogue.json`, so a run never changes under its feet. `factory catalogue check [--project p]` validates every file: front matter, names, roles, kinds, that templates only use known steps, and that every non-generic `agent` is installed (a `.md` file in a known agents folder).

## Adding a new kind of specialist

1. Write `steps/<name>.md` in `$FACTORY_HOME/catalogue/`, a project catalogue folder or `<repo>/.factory/`.
2. Optionally add a template in `pipelines/`.
3. Optionally write a dedicated agent in `agents/<agent>.md` next to `steps/` and name it in the step's `agent`. `factory skill install` installs it with the generic ones. A dedicated agent must accept the same prompt keys as the generic agent for its role. When a step names a dedicated agent that is not installed (`factory agents` lists it under `missing`), the orchestrator runs the generic agent of that role with the same step file and records a skip.
4. Run `factory catalogue check`.

No code changes. The planner sees the new step through `catalogue.json`.

## Validation at plan load

`plan load` refuses the plan, with every problem listed, when:

- a step is unknown, has the wrong role for its place (a gate as `step`, a producer in `gates`), or does not fit the job's `kind`
- a gate appears twice in one group, or a group has more than 6 gates, or a job more than 6 groups
- a group has no gates and its step is not `gates_optional`
- a code group has no `review-code` while its step has `code_review: required` and the project's `defaults.require_code_review` is true (default true)
- a step's `requires` is not met
- `optional_gates` appears in a plan (it belongs to templates only)
- `depends_on` names an unknown job or forms a cycle
- a `code` job names a repo outside the project, or a `files` job names a repo

## Running a job

```
job start        group 1, round 1, phase produce          orchestrator spawns the producer
step sync        output.md DONE                           phase gate (or passed when no gates)
                 output.md BLOCKED                         orchestrator retries once with job round; still BLOCKED, or at the cap: asks
                 output.md QUESTION                        the job pauses
gate sync        all gate files present: outcome
                   PASS                                    group passed
                   CHANGES, round < max_round              job round: round + 1, resume the same producer
                   CHANGES, round = max_round              group capped; must-fix items become open points
job next         next group (phase produce, round 1), or stage merging/delivering after the last group
```

- WAIT FOR ALL: the orchestrator never resumes a producer while a gate of the same round is still running.
- Human gates come last in a round: `gate sync` reports them as `human` only when every agent gate of the round has a verdict and all are PASS. When an agent gate says CHANGES, the human gates of that round are not asked, so a person never approves something that is about to change.
- The producer of a group is resumed (same agent) for every round of that group and for merge conflicts. Each group gets its own agent; the agent id is stored on `job_groups.agent`.
- A later group's producer gets the earlier groups' folders as `previous`, so `fix` reads what `reproduce` did.
- `upstream`: the job folders of `depends_on` jobs. Their `output.md` (contract as built, open points) and the Notes of their last gate round travel through these files. A note that names a later job or its repo must be handled by that job, and its gates check it.

## Human gates

For a gate with `agent: human`:

1. When every agent gate of the round passed, `gate sync` reports it as `human` with the question text from the step's `## Ask` section, with its placeholders `{job_title}` (the job title) and `{output}` (the path of the producer's `output.md`) filled in.
2. The orchestrator asks it with `question ask --job <job> --by gate:<name> --path <output.md>`. The format check applies; the step body must give a valid question.
3. On the answer: `factory gate human <ref> <gate> pass [--note "<note>"]` when the answer is option A, alone or with a note (`A: ship it, fix the label later`), else `factory gate human <ref> <gate> changes --note "<answer>"`. The CLI writes the gate file (`VERDICT: PASS` with the note under Notes, or `VERDICT: CHANGES` with the answer under Must fix), so the round logic is the same as for agent gates.
4. Ask back on a human gate's question has no agent to answer it: the orchestrator answers the follow-up itself from the producer's `output.md` with `question reply <id> --text "<reply>"`.

## Rounds and caps

- `max_round` per group comes from the run's `max_rounds` (default 4, 1 to 10). `run rounds <run-id> <n>` changes it for groups not yet passed.
- After the cap the job moves on; the capped group's Must fix items and Notes go to the PR's Open points (code) or the run report (files).
- A producer still BLOCKED after one retry, or at the cap, becomes a question.
