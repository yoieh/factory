# none

No code host. This is the code host of every project that names none.

The run builds, reviews and merges every job into one local feature branch per repo, runs the full tests and stops. Nothing is pushed and no PR is opened. `run new` sets `no_pr`, so Finish reports per repo the feature branch, the feature worktree and the test result. Publish refuses. Improve commits in its worktree and pushes nothing.

This adapter has no capabilities, so it has no capability sections.

## Setup

Nothing to install or set. To push and open PRs, name another code host adapter in the project:

```sh
"$F" project set <project> integrations.code_host '{"adapter": "<name>"}'
```

## Never

- Push a branch, open a PR or reach a code host in any other way for this project.
- Say that a PR exists. Report the local branches and worktrees instead.
