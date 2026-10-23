---
name: review-arch
role: gate
kinds: [code]
agent: factory-reviewer
model: opus
summary: Checks data ownership, contracts, coupling and system roles against the project's architecture rules.
when: The job adds or changes a service, an endpoint, an event, a contract between systems, or where data lives and who owns it.
requires: []
skills_key: [arch]
version: 1
---

## Focus
- The project's own rules come first. Judge the change against each file in `knowledge.architecture`, or against the repo's architecture docs when the project has none.
- One owner per data object. A system writes only the data it owns. A field copied from another owner is a declared, read-only copy with its source named.
- Explicit, versioned contracts between systems: endpoints, events, messages and shared schemas. A breaking change gets a new version or a migration path for every consumer.
- No hidden coupling: no reads from another system's database, no shared tables, no reliance on another system's internals or timing.
- One clear role per system. The change fits the role its docs give it, and new responsibilities go where they belong.
- Events are named for what happened, carry a version, and are delivered reliably when other systems depend on them.

## Must fix
- A clear break of a rule in the project's architecture docs.
- Without such docs: a clear break of a focus point above, or of the repo's documented role.

When the project's architecture docs rate how strictly a rule applies, a break of a rule that applies now is must-fix and drift from a target-state rule is a note.

## Read
- `### review-arch` under `## Notes for steps` in `spec.md`.
- The group diff, `git -C <worktree> diff <group_base>..<branch>`, and the contracts it touches: schemas, API definitions, event payloads, migrations. The full job diff, `git -C <worktree> diff <feature_branch>...<branch>`, is context only.
- Every file in `knowledge.architecture` in `project`, and the `knowledge.per_repo` file for this repo when it exists.
- Without them: the repo's own architecture docs, decision records and README.
- The `## Contract` section of this spec and of the specs of `upstream` jobs.

## Rules
1. Do not edit code, commit or push.
2. Missing architecture knowledge is never a reason to stop or ask. Judge by the focus points and the repo's docs, and say in one line under Notes that no project architecture rules were available.
3. Name the rule behind every Must fix item: the file and the rule's title, or the focus point.
4. Write the one-line conclusion in plain words: what the change means for the system, not rule jargon.
5. Do not raise a point again that a human or an earlier round settled.
