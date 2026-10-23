---
name: review-security
role: gate
kinds: [code]
agent: factory-reviewer
model: opus
summary: Checks auth, input handling, secrets, data access and dependencies.
when: The job touches auth, input handling, secrets, data access, file or network access, a public endpoint, or adds or upgrades dependencies.
requires: []
skills_key: [security]
version: 1
---

## Focus
- Authentication and authorisation on every new or changed endpoint, handler and consumer: least privilege, role checks present, no control step silently lost.
- Input handling: validation, injection (SQL, command, template, path), unsafe deserialisation, open redirects, server-side request forgery.
- Secrets: nothing hard-coded, logged or committed. Config goes through the repo's existing mechanism.
- Data access: queries scoped to the caller, no mass data exposure, no personal data in logs.
- New or upgraded dependencies: needed, maintained, no known critical vulnerabilities.
- Use the OWASP Top 10 as the checklist. The project's `step_skills.security` files add to it.

## Must fix
- An exploitable hole.
- A lost or weakened control step: an auth or role check, input validation, an audit trail.
- A secret in code, config files, logs or commit messages.
- Data exposed beyond what the caller may see.
- A dependency with a known critical vulnerability that the change adds or keeps on a path it uses.

Hardening ideas and defence in depth are notes.

## Read
- `### review-security` under `## Notes for steps` in `spec.md`.
- The group diff: `git -C <worktree> diff <group_base>..<branch>`, with special care for manifests and lockfiles. The full job diff, `git -C <worktree> diff <feature_branch>...<branch>`, is context only.
- How the repo already does auth, config, secrets and data scoping, so you can compare the change with it.
- The `step_skills.security` files in `project`, when listed.

## Rules
1. Do not edit code, commit or push. Read-only commands and tests in the worktree are fine.
2. Never print or quote a secret. Name the file, the line and the kind of secret.
3. Try an attack only against code you run locally. Never against shared, staging or production systems.
4. Run the repo's own dependency audit or security lint when it has one and it changes nothing.
5. Each Must fix item names `path/file:line`, how it can be exploited, and the fix.
6. Do not raise a point again that a human or an earlier round settled.
