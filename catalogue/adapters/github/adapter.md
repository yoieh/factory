# github

Pushes feature branches to GitHub and opens one draft PR per repo with the `gh` CLI. A script reads PR states for `run prs`, Improve and the dashboard; two more read a PR's checks and mark it ready or merge it for `land`. Only `github.com` as written; for GitHub Enterprise, override `pr_url` in the project's `patterns` and use the instance's host in the commands (`--hostname`, the `origin` parsing).

`$F` is the factory CLI as the skill sets it. Values in `<angle brackets>` come from the run (`run new`, `plan load`, `paths`) or, in Improve, from `improvement start`.

The same steps serve Finish, Publish and Improve:

| Value | Finish and Publish | Improve |
|---|---|---|
| `<dir>` | the repo's `<feature_worktree>` | the group's `<worktree>` |
| `<branch>` | `<feature_branch>` | `<branch>` (`chore/improvements-<batch>`) |
| `<base>` | the repo's `<base_branch>` | the group's `<base_branch>` |
| `<file>` | `pr-<repo>.md` in the run folder | the group's `pr.md` |

## push

In Finish and Publish, push only the repos whose entry in `repos` from `run new` has `can_push`; Finish records the skip for the others.

1. Push:
   ```sh
   git -C "<dir>" push -u origin "<branch>"
   ```
2. A rejected push is never retried with force. Run `git -C "<dir>" fetch origin "<branch>"`, log it (a line in `retro-notes.md` in a run), and stop for that repo with a report.

## open_pr

After the push.

1. Check the login: `gh auth status --hostname github.com`. On a failure, record `"$F" skip add <run-id> "code_host.open_pr" "<repo>: gh is not signed in, branch pushed, open the PR by hand"` (no `--pr`; in Improve, report it) and report the pushed branch and its base for a person to open the PR.
2. The repository, as `<owner>/<name>`, from `origin`. This prints only the path, never the URL or credentials in it, and prints nothing when `origin` is not on `github.com` (then stop for that repo and report it):
   ```sh
   git -C "<dir>" remote get-url origin | sed -nE 's#^(git@github\.com:|ssh://git@github\.com/|https://([^@/]*@)?github\.com/)([^/]+/[^/]+)$#\3#p' | sed -E 's#\.git$##'
   ```
3. Title: the first line of `<file>` without `# `. Body: the rest of the file.
4. Look for an open PR for the same branches:
   ```sh
   gh pr list --repo "<owner>/<name>" --head "<branch>" --base "<base>" --state open --json url --jq '.[0].url // ""'
   ```
5. No open PR: create a draft.
   ```sh
   sed '1d' "<file>" | gh pr create --repo "<owner>/<name>" --head "<branch>" --base "<base>" --title "<title>" --body-file - --draft
   ```
   Leave out `--draft` when the `draft` setting is false. Add `--reviewer "<login>,<login>"` when the `reviewers` setting is not empty; the repo's code owners are requested by GitHub itself. The PR url is the last line of the output. When GitHub refuses a draft (some plans allow drafts only in public repos), stop for that repo and report it; the project can set `draft` to false.
6. An open PR: update it in place.
   ```sh
   sed '1d' "<file>" | gh pr edit "<url>" --title "<title>" --body-file -
   ```
7. Store the url. In a run: `"$F" run pr <run-id> "<url>" --repo <repo>`. In Improve: `"$F" improvement pr <ids> --url "<url>" --branch "<branch>"`.
8. Related PRs and rework run steps 3 to 7 again and must land in step 6. When step 5 runs for a repo that already had a PR, log it in `retro-notes.md` and tell the user.

## pr_checks and land_pr

Scripts the core runs itself in Land: `land check` and `land start` run `pr_checks`, and `land apply` runs `land_pr` only when the lander said PASS, the project's `defaults.land` is `ready` or `merge`, every check passes, the PR merges cleanly and its head is the commit the lander judged. Never run them yourself, and never run `gh pr ready` or `gh pr merge` yourself either.

- `land_pr` with `ready` marks a draft PR ready for review. With `merge` it marks it ready and merges it with the `merge_method` setting (`squash`, `merge` or `rebase`), pinned to the judged commit. It never uses admin rights, auto-merge or branch deletion, so branch protection, required reviews and code owners still apply: when GitHub refuses the merge, `land apply` reports it and changes nothing else.

## pr_template

Read the template fresh on every run, never from memory.

1. When the repo has its own template, use its sections in its order. Take the first file that exists in `<dir>`: `.github/pull_request_template.md`, `.github/PULL_REQUEST_TEMPLATE.md`, `docs/pull_request_template.md`, `pull_request_template.md`. Ignore template folders (`.github/PULL_REQUEST_TEMPLATE/`): they need a human's choice. Keep its checkboxes and tick only what the run proved.
2. Otherwise use `pr-template.md` in this folder.
3. The title format of `pr-template.md` applies in both cases.

## Setup

- Install `gh` (https://cli.github.com) and run `gh auth login` for `github.com` with access to every repo of the project. `gh auth status` must pass.
- Each repo needs an `origin` remote on `github.com` that `git push` already works with.
- Optional settings: `draft` (default true), `reviewers` (GitHub logins or `org/team` names, default none) and `merge_method` (`squash`, `merge` or `rebase`, default `squash`, used only by Land). Without `reviewers`, the repo's code owners are the reviewers GitHub adds by itself.

## Never

- Approve or close a PR (`gh pr review --approve`, `gh pr close`), or mark one ready or merge it (`gh pr ready`, `gh pr merge`) other than through the core's `land apply`.
- Force-push in any form, or push the base branch or any branch other than the run's feature branches and `chore/improvements-*`.
- Print a token: no `gh auth token`, no `gh auth status --show-token`, no `GH_TOKEN` or `GITHUB_TOKEN` in output, and never print the remote URL.
- Write closing keywords (`Closes`, `Fixes`, `Resolves` followed by an issue) in a PR text: they close issues on merge, which writes to the tracker. Refer to an issue as `#<n>` only.
- Reach GitHub any other way than the commands above (no `gh api` writes, no web pages).

## Scripts to build

`scripts/pr_state.py --url <pr_url>`, Python 3.10 standard library, run by the core with cwd = this folder:

- Refuse a url that does not match the `pr_url` pattern: exit 1.
- Run `gh pr view <url> --json state,url` with a 25 second timeout and no prompt (`GH_PROMPT_DISABLED=1`).
- `gh` exit 4 (authentication required): exit 3. `gh` missing, any other `gh` failure or a timeout: exit 1 with one line on stderr, never the token or the environment.
- Map `state`: `OPEN` to `OPEN`, `MERGED` to `MERGED`, `CLOSED` to `DECLINED`.
- stdout, on exit 0: `{"state": "OPEN"|"MERGED"|"DECLINED", "url": "<the url>"}`.

`scripts/pr_checks.py --url <pr_url>`, the same rules and exit codes:

- `gh pr view <url> --json state,url,isDraft,headRefOid,mergeable,reviewDecision,statusCheckRollup`.
- A check run passes when it is completed with `SUCCESS`, `NEUTRAL` or `SKIPPED`, and still runs while it is not completed; a status passes on `SUCCESS` and still runs on `PENDING` or `EXPECTED`. Anything else fails.
- stdout: `{"state", "url", "draft", "head_sha", "mergeable": "MERGEABLE"|"CONFLICTING"|"UNKNOWN", "review", "checks": "passing"|"pending"|"failing"|"none", "failing": [names], "pending": [names]}`.

`scripts/land_pr.py --url <pr_url> --action ready|merge --head <sha> --method <merge_method>`, the same rules and exit codes:

- Refuse a `--head` that is not a full commit sha, and a method other than `squash`, `merge` or `rebase`.
- `gh pr view <url> --json state,isDraft,headRefOid,url`: refuse a PR that is not open, or whose head is not `--head`.
- A draft: `gh pr ready <url>`. With `merge`, then `gh pr merge <url> --<method> --match-head-commit <sha>`; never `--admin`, `--auto` or `--delete-branch`.
- stdout: `{"action": "ready"|"merged", "url": "<the url>"}`.
