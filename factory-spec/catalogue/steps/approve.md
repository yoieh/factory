---
name: approve
role: gate
kinds: [code, files]
agent: human
summary: A person approves the result or asks for changes.
when: A person must sign off before the job lands, for example a risky migration, a change the issue asks someone to approve, or a deliverable for a decision.
requires: []
version: 1
---

No agent runs this step. The orchestrator asks the question in the Ask section at the end, with the job title and the output path filled in. Answer A approves. Any other answer asks for changes, and its text becomes the Must fix item of the next round.

## Focus
- The result does what the spec and the issue ask.
- The risks and Open points in the producer's `output.md` are acceptable.
- For a migration: the dry run, the rollback steps and the rollout order.
- For a files job: the deliverable answers the questions it was made for.

## Must fix
What the person writes when they ask for changes. Nothing else.

## Read
- The producer's `output.md`.
- Code job: the job's commits. Files job: the files under `out/`.
- The other gate files of the same round. This question is asked only after every agent gate of the round passed.

## Rules
1. Answer with one letter. To ask for changes, write B followed by what to change, for example `B: keep the old field until the next release`.
2. A request for changes starts a new round of the same producer, up to the round cap. Ask for everything you need in one answer.
3. After the cap, what you asked for goes to the PR's Open points (code) or the run's `report.md` (files).

## Ask

QUESTION: Approve {job_title}?

**Why I ask:** This job needs a person's sign-off before it lands. The result is in `{output}`.

**Options**
- **A (recommended):** Approve. The job moves on and lands with the rest of the run.
- **B:** Ask for changes, and write what to change after the letter. The producer gets your text as a must-fix item and starts a new round.

**Status:** The producer is done. The job waits for this answer.
