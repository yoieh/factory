# git

Push only. Finish, Publish and Improve push the branch to the repo's `origin` remote, and a person opens the PR by hand. Use it for a host without a CLI or API.

`$F` is the factory CLI as the skill sets it. Values in `<angle brackets>` come from the run (`run new`, `plan load`, `job show`, `paths`) or, in Improve, from `improvement start`.

## push

In Finish and Publish, push every repo whose entry in `repos` from `run new` has `can_push`: `<dir>` is its `<feature_worktree>` and `<branch>` its `<feature_branch>`. A repo without `can_push` has no `origin`; Finish records its skip and goes on. In Improve, `<dir>` is the group's `<worktree>` and `<branch>` its `<branch>` (`chore/improvements-<batch>`).

1. In Improve only, check that `origin` exists, without printing its URL (it may hold credentials):
   ```sh
   git -C "<dir>" remote get-url origin > /dev/null 2>&1 && echo ok
   ```
   No `ok`: push nothing for that group; keep the worktree and branch and report them.
2. Push:
   ```sh
   git -C "<dir>" push -u origin "<branch>"
   ```
3. A rejected push (the remote branch has commits the local branch lacks) is never retried with force. Run `git -C "<dir>" fetch origin "<branch>"`, log the rejection (a line in `retro-notes.md` in a run), and stop for that repo with a report of both branch heads.
4. There is no PR url, so never call `run pr` or `improvement pr`. In a run, record the missing PR as a skip without `--pr`:
   ```sh
   "$F" skip add <run-id> "code_host.open_pr" "<repo>: no PR opened, open it by hand from the pushed branch"
   ```
   Report per repo the pushed branch and its base branch, and say that a person opens the PR on the host. In Improve, also list the item ids, so the person can record the PR with `factory improvement pr <ids> --url <url> --branch <branch>` once it exists.

## Setup

Each repo needs an `origin` remote the user can push to (an SSH key or a credential helper that already works for `git push`). Nothing else.

## Never

- Force-push in any form: `--force`, `--force-with-lease`, `+<refspec>`, `--mirror`, `--delete`, or pushing `--all` or `--tags`.
- Push the base branch or any branch other than the run's feature branches and, in Improve, `chore/improvements-*`.
- Print the remote URL or the git credential helper's output.
- Merge, or open the PR on the host's web page for the user.
