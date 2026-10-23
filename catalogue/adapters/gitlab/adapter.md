# gitlab

Pushes feature branches to GitLab and opens one draft merge request (the factory's "PR") per repo with the `glab` CLI. A script reads merge request states for `run prs`, Improve and the dashboard. Only `gitlab.com` as written; for a self-managed instance, override `pr_url` in the project's `patterns` and use the instance's host in the commands (`--hostname`, the `origin` parsing).

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

1. Check the login: `glab auth status --hostname gitlab.com`. On a failure, record `"$F" skip add <run-id> "code_host.open_pr" "<repo>: glab is not signed in, branch pushed, open the merge request by hand"` (no `--pr`; in Improve, report it) and report the pushed branch and its base for a person to open the merge request.
2. The project path (`<group>/<name>`, subgroups included) from `origin`. This prints only the path, never the URL or credentials in it, and prints nothing when `origin` is not on `gitlab.com` (then stop for that repo and report it):
   ```sh
   git -C "<dir>" remote get-url origin | sed -nE 's#^(git@gitlab\.com:|ssh://git@gitlab\.com/|https://([^@/]*@)?gitlab\.com/)([^@]+)$#\3#p' | sed -E 's#\.git$##'
   ```
3. Title: the first line of `<file>` without `# `. Description: the rest of the file.
4. Look for an open merge request for the same branches:
   ```sh
   glab mr list --repo "<path>" --source-branch "<branch>" --target-branch "<base>" --output json
   ```
   An empty list means none. Otherwise keep the first item's `iid` and `web_url`.
5. None open: create a draft.
   ```sh
   sed '1d' "<file>" | glab mr create --repo "<path>" --source-branch "<branch>" --target-branch "<base>" --title "<title>" --description-file - --draft --yes
   ```
   Leave out `--draft` when the `draft` setting is false. Add `--reviewer "<user>,<user>"` when the `reviewers` setting is not empty; approval rules and code owners apply by themselves. The url is the output line that matches the `pr_url` pattern.
6. One open: update it in place. Do not pass `--reviewer`: it would replace the reviewers.
   ```sh
   sed '1d' "<file>" | glab mr update <iid> --repo "<path>" --title "<title>" --description-file - --yes
   ```
7. Store the url. In a run: `"$F" run pr <run-id> "<web_url>" --repo <repo>`. In Improve: `"$F" improvement pr <ids> --url "<web_url>" --branch "<branch>"`.
8. Related PRs and rework run steps 3 to 7 again and must land in step 6. When step 5 runs for a repo that already had a merge request, log it in `retro-notes.md` and tell the user.

## pr_template

Read the template fresh on every run, never from memory.

1. When the repo has `.gitlab/merge_request_templates/Default.md` in `<dir>`, use its sections in its order. Ignore other templates in that folder: they need a human's choice. Keep its checkboxes and tick only what the run proved.
2. Otherwise use `pr-template.md` in this folder.
3. The title format of `pr-template.md` applies in both cases.

## Setup

- Install `glab` (https://gitlab.com/gitlab-org/cli) and run `glab auth login` for `gitlab.com` with the `api` scope and access to every repo of the project. `glab auth status` must pass.
- Each repo needs an `origin` remote on `gitlab.com` that `git push` already works with.
- Optional settings: `draft` (default true) and `reviewers` (GitLab usernames, default none). Without `reviewers`, approval rules and code owners apply as GitLab sets them.

## Never

- Merge, approve, close or mark ready a merge request (`glab mr merge`, `glab mr approve`, `glab mr close`, `glab mr update --ready`), or set auto-merge.
- Force-push in any form, or push the base branch or any branch other than the run's feature branches and `chore/improvements-*`.
- Print a token (`glab auth status --show-token`, `GITLAB_TOKEN`, the glab config file) or a remote URL.
- Write closing keywords (`Closes`, `Fixes`, `Resolves` followed by an issue) in a merge request text: they close issues on merge, which writes to a tracker.
- Reach GitLab any other way than the commands above (no `glab api` writes, no web pages).

## Scripts to build

`scripts/pr_state.py --url <pr_url>`, Python 3.10 standard library, run by the core with cwd = this folder:

- Refuse a url that does not match the `pr_url` pattern: exit 1. Split it into the project path and the `iid`.
- `glab auth status --hostname gitlab.com` fails: exit 3.
- Run `glab mr view <iid> --repo <path> --output json` with a 25 second timeout. `glab` missing, a failure or a timeout: exit 1 with one line on stderr, never the token or the environment.
- Map `state`: `opened` and `locked` to `OPEN`, `merged` to `MERGED`, `closed` to `DECLINED`.
- stdout, on exit 0: `{"state": "OPEN"|"MERGED"|"DECLINED", "url": "<the url>"}`.
