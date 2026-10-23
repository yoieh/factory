# 10 Dashboard

`factory serve [--port 8765]`: a stdlib HTTP server (`http.server` with threads) and one self-contained `dashboard.html` (no CDN, no build step). It reads through the core's Python functions and writes only answers, follow-ups, decisions, issue edits and archive flags, through the same functions as the CLI.

## Pages

All pages share a sidebar: Inbox (count of open questions and plans to confirm), Issues, Projects, Improvements (count to review), Agents, and the runs list with a search box (issue key, repo, feature) and a menu for active (not done, not archived), archived or all. A project picker at the top filters every page (default: all projects). The open view lives in the URL hash so links can be shared locally. Every cost shows the date of the price table it uses (11).

| Page | Hash | Shows |
|---|---|---|
| Inbox | `#inbox`, `#inbox/q<id>` | One open question or plan confirm at a time in the middle, as a row of chips to walk with arrow keys. Right rail: the job's stage, group, round and files. Empty: the runs working now. |
| Run | `#<run-id>`, `#<run-id>/<job-id>`, `#<run-id>/<job-id>/q<id>` | Header with the feature's first sentence (rest folded) and a strip per waiting question. Views: Loop graph, List, Timeline. Right pane: repos and PR states, skips, run files, cost by node, answered questions, events; with a job open, its pipeline, groups, rounds, verdicts, files and cost. |
| Issues | `#issues`, `#issues/<KEY-n>` | Filterable list; one issue with body, links, history; create and edit. Shows the `/factory <KEY-n>` command to copy. |
| Projects | `#projects`, `#projects/<name>` | Repos, integrations with availability, knowledge paths that exist, catalogue (steps and templates with their source), skill installs and drift. Read-only. |
| Improvements | `#improvements` | Every improvement, filtered by status, target and project; evidence, proposal, run, `seen_in`; accept, reject, mark done, back to review, with a note. |
| Agents | `#agents`, `#agents/<name>` | The connected agents (04): name, description, roles, last seen, how many answers, follow-ups, improvement decisions, runs started and PR moves; one agent with its roles, its latest answers, each linking to its question, its latest improvement decisions with their notes, the runs it started, and its latest PR moves (review, fix, merge, deploy), each linking to its run. An improvement card says who decided it, and a run an agent started names the agent. Read-only. |

### Loop graph with pipelines

The run-level phases are fixed boxes: Intake, Planner, Confirm, Jobs, Finish, Retrospective. Confirm turns done as soon as the plan confirm is answered yes, before the session picks the answer up; any other answer shows the Planner running again. The Jobs box expands into one lane per job showing its pipeline: one box per group (producer name, with its gates as chips under it), the job token on the current group, and the gate chips filling in green (PASS), red (CHANGES) or grey (pending) per round. A `?` on a token opens its question. Clicking a box opens the step's catalogue entry (summary, focus, who runs it, model) and where this run stands there. Keyboard: up and down walk the run-level phases, `j` and `k` the jobs, left and right the groups of the selected job, Esc closes.

### Timeline

One lane per job: a bar per producer round and per gate round (green PASS, red CHANGES, dashed while running), a merge or deliver mark, a yellow line while the job waits for an answer. A run lane for planning, confirm, finish and retrospective. Long idle stretches shrink to a hatched gap with their length.

### Question view

The question as markdown, the options as cards with the recommended one highlighted, the `<details>` block folded, the thread (latest message shown, older folded), an answer box docked below. A click on an option puts its letter in the box; Cmd/Ctrl+Enter sends; focus moves to the next item. **Ask back** sends a follow-up instead. A plan confirm shows the jobs with their pipelines, an Approve button (answers "yes") and a field for changes.

## API

GET (JSON):

| Path | Returns |
|---|---|
| `/api/runs?project=&archived=` | Runs with status, counts, cost. |
| `/api/runs/<id>` | `run show` plus usage. |
| `/api/jobs?project=` | Every job of every active run. |
| `/api/questions?status=open,clarify` | Questions with threads. |
| `/api/issues?project=&status=&type=&label=&q=` | Issues. |
| `/api/issues/<KEY-n>` | One issue with body text, links, events. |
| `/api/projects`, `/api/projects/<name>` | Projects, resolved and redacted (08). |
| `/api/catalogue?project=` | Merged catalogue. |
| `/api/improvements?project=&status=` | Improvements. |
| `/api/inbox?project=&as=&role=` | `inbox` (04). |
| `/api/events?since=&run=&kind=&limit=&wait=&timeout=&as=` | `events` (04); `kind` is comma-separated, `wait=1` waits at most `timeout` seconds (default 30, at most 60). |
| `/api/agents`, `/api/agents/<name>` | `agent list`, `agent show`. |
| `/api/file?path=` | A `.md` or `.json` file under `runs/`, `improve/` or `issues/`, as text. |
| `/api/image?path=` | A `.png` under `runs/`, checked by magic bytes. |

POST (`Content-Type: application/json` only):

| Path | Body | Does |
|---|---|---|
| `/api/questions/<id>/answer` | `{text, by}` | `question answer` |
| `/api/questions/<id>/clarify` | `{text, by}` | `question clarify` (not delivered) |

Answers and follow-ups from the dashboard have author `dashboard`, or the connected agent named in `by`, and count as the user's, like any `question answer` (09, Questions). A question's thread shows each message's author, so an answer written with `--by <name>` shows that name.
| `/api/improvements/<id>/decide` | `{status, note, by}` | `improvement decide`; author `dashboard`, or the connected agent named in `by` |
| `/api/runs/<id>/archive` | `{archived: bool}` | archive or unarchive |
| `/api/issues` | `{project, title, type, priority, labels, body}` | `issue add` |
| `/api/issues/<KEY-n>` | any of `{status, priority, title, labels, body}` | `issue set` / `issue edit` |

## Security

- Bind 127.0.0.1 only. Reject requests whose `Host` is not `127.0.0.1:<port>`, `localhost:<port>` or a name in `config.json` `dashboard.hosts` (on any port), and POSTs whose `Origin` is present and neither local nor `http(s)://` on such a name. `dashboard.hosts` is for a proxy such as `tailscale serve` in front of the local server; it never changes the bind address.
- File reads: resolve the path, require it under the allowed folders, allow only the listed suffixes, refuse symlinks that leave the folder.
- Text limits on POST bodies (answers and notes 4000 characters, issue bodies 100 000).
- No secrets in any response; project responses are redacted.
- Render markdown with an escaping renderer; no raw HTML except the `<details>` and `<summary>` tags of questions, rebuilt by the renderer, never passed through.

## Background work

- `usage sync` on each poll of an open run (cheap: reads from stored offsets).
- PR states: every 5 minutes for open and unchecked PRs through `pr_state`; final states are not read again. The poller reloads project config on every pass, so adding a code host needs no restart.
- Polling: the page polls every 3 seconds while visible and every 30 seconds when hidden.

## Look

Light and dark theme following the system, with a toggle remembered by the browser. Plain, calm, readable; colour only for status. No brand colours in the core; a project may not restyle the dashboard.
