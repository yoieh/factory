# bitbucket

Pushes feature branches to Bitbucket Cloud and opens one draft PR per repo through the REST API, with the repo's default reviewers. Two scripts do the API calls: `open_pr` (run by the orchestrator through the CLI) and `pr_state` (run by the core for `run prs`, Improve and the dashboard). The orchestrator never sees the token: the core reads it from the environment or a credential file and hands it only to the script.

`$F` is the factory CLI as the skill sets it. Values in `<angle brackets>` come from the run (`run new`, `plan load`, `paths`) or, in Improve, from `improvement start`.

The same steps serve Finish, Publish and Improve:

| Value | Finish and Publish | Improve |
|---|---|---|
| `<dir>` | the repo's `<feature_worktree>` | the group's `<worktree>` |
| `<branch>` | `<feature_branch>` | `<branch>` (`chore/improvements-<batch>`) |
| `<base>` | the repo's `<base_branch>` | the group's `<base_branch>` |
| `<file>` | `pr-<repo>.md` in the run folder | the group's `pr.md` |
| `<repo>` | the repo's name | the group's `repo` (for the factory source, see open_pr step 1) |

## push

In Finish and Publish, push only the repos whose entry in `repos` from `run new` has `can_push`; Finish records the skip for the others.

1. Push:
   ```sh
   git -C "<dir>" push -u origin "<branch>"
   ```
2. A rejected push is never retried with force. Run `git -C "<dir>" fetch origin "<branch>"`, log it (a line in `retro-notes.md` in a run), and stop for that repo with a report.

## open_pr

After the push. When `"$F" project show <project> --json` lists `open_pr` as unavailable (no API token in the environment or the credential files), record `"$F" skip add <run-id> "code_host.open_pr" "<repo>: no Bitbucket API token, branch pushed, open the PR by hand"` (no `--pr`; in Improve, report it) and report the pushed branch and its base for a person to open the PR.

1. Run the script through the CLI. It finds an open PR for the same branches and updates its title and description, or creates a draft PR with the repo's default reviewers:
   ```sh
   "$F" adapter run bitbucket open_pr --project <project> --repo "<repo>" --source_branch "<branch>" --target_branch "<base>" --pr_file "<file>"
   ```
   `--repo` fills `{repo_path}` from the project config. For the factory source in Improve, which is not a project repo, pass `--repo_path "<repo_path>" --remote_url "<remote_url>"` from `improvement start` instead of `--repo`. The script reads the title from the first line of `<file>` and the description from the rest. `{draft}` comes from the project's `draft` setting; the CLI fills it.
2. The result is `{"action": "created"|"updated", "url": "<pr url>"}`. Exit 3 means the credentials are missing or refused: record `"$F" skip add <run-id> "code_host.open_pr" "<repo>: Bitbucket credentials refused, branch pushed, open the PR by hand"` (no `--pr`; in Improve, report it) and report the pushed branch and its base for a person to open the PR. Any other failure: report the script's one-line error and stop for that repo.
3. Store the url. In a run: `"$F" run pr <run-id> "<url>" --repo <repo>`. In Improve: `"$F" improvement pr <ids> --url "<url>" --branch "<branch>"`.
4. Related PRs and rework run step 1 again; the result must say `updated`. `created` for a repo that already had a PR means a second PR: log it in `retro-notes.md` and tell the user.

## pr_template

Read `pr-template.md` in this folder fresh on every run, never from memory. Bitbucket Cloud has no template files in repos.

## Setup

- Create a Bitbucket API token for your Atlassian account with the scopes `read:user:bitbucket`, `read:repository:bitbucket`, `read:pullrequest:bitbucket` and `write:pullrequest:bitbucket`.
- Set `BITBUCKET_USER_EMAIL` (the account's email) and `BITBUCKET_API_TOKEN` in the environment, or as `KEY=value` lines (`export` and quotes allowed) in `~/.bitbucket.env` or `~/.env`. Never put them in project config.
- Each repo needs an `origin` remote on `bitbucket.org` that `git push` already works with (an SSH key or an HTTPS credential helper).
- Optional setting: `draft` (default true).
- Check with `"$F" project show <project>`: `open_pr` and `pr_state` must be available.

## Never

- Call the Bitbucket API any other way: no `curl` by hand, no other tools. Only the two scripts.
- Read, `source`, `cat` or print a credential file, or print the environment (`env`, `printenv`, `set`).
- Merge, approve or decline a PR, or remove reviewers.
- Force-push in any form, or push the base branch or any branch other than the run's feature branches and `chore/improvements-*`.
- Print a remote URL: an HTTPS remote may hold a password.

## Scripts to build

Both are Python 3.10 standard library, run by the core with cwd = this folder, the 30 second timeout and the two credential variables in the environment. Common rules:

- `BITBUCKET_USER_EMAIL` or `BITBUCKET_API_TOKEN` empty: exit 3. HTTP 401 or 403 from the API: exit 3.
- Requests go only to `https://api.bitbucket.org/2.0/`, with Basic auth (`email:token`), `Accept: application/json` and a 10 second timeout each.
- Any other failure: exit 1 with one line on stderr: the HTTP status and the API's `error.message`, never the token, a header or the environment.
- stdout is one JSON object, only on exit 0.

`scripts/open_pr.py --repo-path <repo_path> --source <branch> --target <base> --pr-file <file> --draft <true|false>`:

1. Read `origin` with `git -C <repo_path> remote get-url origin` and parse `<workspace>/<slug>` from `git@bitbucket.org:<workspace>/<slug>.git`, `ssh://git@bitbucket.org/...` or `https://[user[:password]@]bitbucket.org/...`. Another host: exit 1. Never print the URL.
2. Read `<file>`: the title is the first line without `# ` (at most 255 characters), the description is the rest. An empty title: exit 1.
3. `GET repositories/<workspace>/<slug>/pullrequests` with `q=source.branch.name="<branch>" AND destination.branch.name="<base>" AND state="OPEN"`. One found: `PUT repositories/<workspace>/<slug>/pullrequests/<id>` with `{"title", "description"}`, and print `{"action": "updated", "url": "<links.html.href>"}`. Reviewers stay as they are.
4. None found: `GET user` for the author's `uuid`; `GET repositories/<workspace>/<slug>/effective-default-reviewers?pagelen=100`, following `next`; drop the author. Then `POST repositories/<workspace>/<slug>/pullrequests` with `{"title", "description", "source": {"branch": {"name": "<branch>"}}, "destination": {"branch": {"name": "<base>"}}, "draft": <draft>, "reviewers": [{"uuid": "..."}]}`, and print `{"action": "created", "url": "<links.html.href>"}`. When the default reviewers cannot be read, create the PR without them and add `"reviewers": "unread"` to the output.

`scripts/pr_state.py --url <pr_url>`:

1. Refuse a url that does not match the `pr_url` pattern: exit 1.
2. `GET repositories/<workspace>/<slug>/pullrequests/<id>`.
3. stdout: `{"state": "<state>", "url": "<the url>"}`, where `state` is the API's `state` as is (`OPEN`, `MERGED`, `DECLINED` or `SUPERSEDED`). Any other value: exit 1.
