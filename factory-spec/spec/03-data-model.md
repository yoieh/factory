# 03 Data model

One SQLite database, `$FACTORY_HOME/factory.db`, WAL mode, `foreign_keys = ON`, `busy_timeout = 5000`. Only the CLI (and the dashboard server, through the same Python functions) writes it. Markdown and JSON content stays in files; columns hold state and absolute paths.

## Migrations

- `meta(key, value)` holds `schema_version`.
- On connect the core runs every migration above the stored version inside one transaction, then stores the new version. Foreign keys are off during a migration, so a table rebuild cannot cascade, and `foreign_key_check` must pass before the commit.
- Version 2 rebuilds `jobs` to add the stage `dropped`. Version 3 adds `connected_agents` and fills it from the authors of earlier answers and follow-ups. Version 4 adds `improvements.decided_by`. Version 5 adds `runs.started_by`. Version 6 adds `connected_agents.roles`. The schema below is version 6.
- Migrations only add tables, columns and indexes, or rebuild a table with a copy when a CHECK changes. They never drop user data.

## Schema

```sql
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
  name        TEXT PRIMARY KEY,
  key         TEXT NOT NULL UNIQUE,
  config_path TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  archived_at TEXT
);

CREATE TABLE IF NOT EXISTS installs (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  scope        TEXT NOT NULL CHECK (scope IN ('user', 'repo', 'plugin')),
  path         TEXT NOT NULL UNIQUE,
  project      TEXT REFERENCES projects(name) ON DELETE SET NULL,
  version      TEXT NOT NULL,
  installed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS issues (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  key          TEXT NOT NULL UNIQUE,
  project      TEXT NOT NULL REFERENCES projects(name),
  type         TEXT NOT NULL DEFAULT 'task'
               CHECK (type IN ('feature', 'bug', 'task', 'research', 'improvement')),
  title        TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'open'
               CHECK (status IN ('open', 'ready', 'in_progress', 'in_review', 'done', 'closed')),
  priority     TEXT NOT NULL DEFAULT 'medium' CHECK (priority IN ('high', 'medium', 'low')),
  labels       TEXT NOT NULL DEFAULT '[]',
  body_path    TEXT NOT NULL,
  source       TEXT NOT NULL DEFAULT 'local',
  external_key TEXT,
  external_url TEXT,
  external_status TEXT,
  synced_at    TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  closed_at    TEXT,
  UNIQUE (source, external_key)
);

CREATE TABLE IF NOT EXISTS issue_links (
  issue_key  TEXT NOT NULL REFERENCES issues(key) ON DELETE CASCADE,
  kind       TEXT NOT NULL CHECK (kind IN ('run', 'pr', 'issue', 'improvement')),
  ref        TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (issue_key, kind, ref)
);

CREATE TABLE IF NOT EXISTS runs (
  id             TEXT PRIMARY KEY,
  project        TEXT NOT NULL,
  feature        TEXT NOT NULL,
  issue_key      TEXT,
  issue_url      TEXT,
  slug           TEXT NOT NULL,
  feature_branch TEXT NOT NULL,
  parallel       INTEGER NOT NULL DEFAULT 3 CHECK (parallel >= 1),
  max_rounds     INTEGER NOT NULL DEFAULT 4 CHECK (max_rounds BETWEEN 1 AND 10),
  confirm_plan   INTEGER NOT NULL DEFAULT 0,
  no_pr          INTEGER NOT NULL DEFAULT 0,
  status         TEXT NOT NULL DEFAULT 'planning'
                 CHECK (status IN ('planning', 'confirm', 'running', 'finishing', 'done', 'failed')),
  archived_at    TEXT,
  created_at     TEXT NOT NULL,
  started_by     TEXT            -- human, dashboard or a connected agent; added by migration 0005
);

CREATE TABLE IF NOT EXISTS run_repos (
  run_id        TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  repo          TEXT NOT NULL,
  position      INTEGER NOT NULL,
  path          TEXT NOT NULL,
  base_branch   TEXT NOT NULL,
  pr_url        TEXT,
  pr_state      TEXT,
  pr_checked_at TEXT,
  can_push      INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (run_id, repo)
);

CREATE TABLE IF NOT EXISTS jobs (
  run_id     TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  id         TEXT NOT NULL,
  position   INTEGER NOT NULL,
  kind       TEXT NOT NULL DEFAULT 'code' CHECK (kind IN ('code', 'files')),
  repo       TEXT,
  title      TEXT NOT NULL,
  depends_on TEXT NOT NULL DEFAULT '[]',
  reads      TEXT NOT NULL DEFAULT '[]',
  template   TEXT,
  stage      TEXT NOT NULL DEFAULT 'queued'
             CHECK (stage IN ('queued', 'running', 'merging', 'delivering', 'done', 'dropped')),
  group_pos  INTEGER NOT NULL DEFAULT 0,
  branch     TEXT,
  worktree   TEXT,
  conflict   INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (run_id, id),
  CHECK (kind = 'files' OR repo IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS job_groups (
  run_id    TEXT NOT NULL,
  job_id    TEXT NOT NULL,
  position  INTEGER NOT NULL,
  step      TEXT NOT NULL,
  gates     TEXT NOT NULL DEFAULT '[]',
  phase     TEXT NOT NULL DEFAULT 'pending'
            CHECK (phase IN ('pending', 'produce', 'gate', 'passed', 'capped')),
  round     INTEGER NOT NULL DEFAULT 0,
  max_round INTEGER NOT NULL DEFAULT 4,
  base_sha  TEXT,
  agent     TEXT,
  PRIMARY KEY (run_id, job_id, position),
  FOREIGN KEY (run_id, job_id) REFERENCES jobs(run_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS verdicts (
  run_id    TEXT NOT NULL,
  job_id    TEXT NOT NULL,
  group_pos INTEGER NOT NULL,
  round     INTEGER NOT NULL,
  gate      TEXT NOT NULL,
  verdict   TEXT NOT NULL CHECK (verdict IN ('PASS', 'CHANGES', 'QUESTION')),
  path      TEXT NOT NULL,
  PRIMARY KEY (run_id, job_id, group_pos, round, gate),
  FOREIGN KEY (run_id, job_id) REFERENCES jobs(run_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS questions (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  job_id      TEXT,
  asked_by    TEXT NOT NULL,
  text        TEXT NOT NULL,
  path        TEXT,
  answer      TEXT,
  status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'clarify', 'answered', 'resolved')),
  asked_at    TEXT NOT NULL,
  answered_at TEXT
);

CREATE TABLE IF NOT EXISTS question_messages (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  question_id  INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL CHECK (kind IN ('answer', 'clarify', 'reply')),
  author       TEXT NOT NULL,
  text         TEXT NOT NULL,
  path         TEXT,
  at           TEXT NOT NULL,
  delivered_at TEXT
);

CREATE TABLE IF NOT EXISTS connected_agents (
  name        TEXT PRIMARY KEY,
  description TEXT,
  added_at    TEXT NOT NULL,
  seen_at     TEXT,
  roles       TEXT NOT NULL DEFAULT '[]'   -- JSON list of review, fix, merge, deploy; added by migration 0006
);

CREATE TABLE IF NOT EXISTS events (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id  TEXT REFERENCES runs(id) ON DELETE CASCADE,
  job_id  TEXT,
  at      TEXT NOT NULL,
  kind    TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS sessions (
  run_id     TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  session_id TEXT NOT NULL,
  added_at   TEXT NOT NULL,
  PRIMARY KEY (run_id, session_id)
);

CREATE TABLE IF NOT EXISTS transcripts (
  path       TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  run_id     TEXT,
  job_id     TEXT NOT NULL DEFAULT '',
  node       TEXT,
  round      INTEGER NOT NULL DEFAULT 0,
  offset     INTEGER NOT NULL DEFAULT 0,
  recent     TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS usage (
  run_id         TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  job_id         TEXT NOT NULL DEFAULT '',
  node           TEXT NOT NULL,
  round          INTEGER NOT NULL DEFAULT 0,
  model          TEXT NOT NULL,
  input          INTEGER NOT NULL DEFAULT 0,
  cache_write_5m INTEGER NOT NULL DEFAULT 0,
  cache_write_1h INTEGER NOT NULL DEFAULT 0,
  cache_read     INTEGER NOT NULL DEFAULT 0,
  output         INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (run_id, job_id, node, round, model)
);

CREATE TABLE IF NOT EXISTS improvements (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id     TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  project    TEXT NOT NULL,
  key        TEXT NOT NULL,
  target     TEXT NOT NULL,
  repo       TEXT,
  location   TEXT,
  title      TEXT NOT NULL,
  evidence   TEXT NOT NULL,
  proposal   TEXT NOT NULL,
  impact     TEXT NOT NULL DEFAULT 'medium' CHECK (impact IN ('high', 'medium', 'low')),
  status     TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'accepted', 'rejected', 'done')),
  note       TEXT,
  seen_in    TEXT NOT NULL DEFAULT '[]',
  issue_key  TEXT,
  pr_url     TEXT,
  branch     TEXT,
  created_at TEXT NOT NULL,
  decided_at TEXT,
  decided_by TEXT,            -- human, dashboard, orchestrator or a connected agent; added by migration 0004
  UNIQUE (run_id, key)
);

CREATE INDEX IF NOT EXISTS issues_by_project ON issues (project, status);
CREATE INDEX IF NOT EXISTS questions_by_run ON questions (run_id, status);
CREATE INDEX IF NOT EXISTS question_messages_by_question ON question_messages (question_id, id);
CREATE INDEX IF NOT EXISTS question_messages_by_author ON question_messages (author, id);
CREATE INDEX IF NOT EXISTS events_by_run ON events (run_id, id);
CREATE INDEX IF NOT EXISTS sessions_by_id ON sessions (session_id, added_at);
CREATE INDEX IF NOT EXISTS runs_by_project ON runs (project, status);
```

Notes:

- `projects.config_path` points at `project.json`; project settings live in that file, not in columns. The table exists so issues, runs and installs can reference a project and so lookup by name is cheap.
- `jobs.repo` is null only for `files` jobs.
- `runs.no_pr` is cleared by `run pr` (a Publish opened PRs), so a later rework updates them.
- `job_groups` is the job's pipeline, one row per group. `round` and `max_round` are per group. `base_sha` is set when the group starts (code jobs): the commit its gates diff against.
- `connected_agents` holds the outside tools that answer for the user (04, Connected agents), by name only; `question_messages.author` refers to it by name, without a foreign key, because `human` and `dashboard` are authors too and an agent's messages outlive its removal.
- `events.run_id` is null for events outside a run (project, issue and improve events).
- Skips are events of kind `skip` with payload `{"dependency", "effect", "pr": bool}`.
- `improvements.target` is validated against the project's `improvement_targets` at `retro load`, not by a CHECK, because targets are project config.

## State machines

Run:

```
planning ──► confirm ──► running ──► finishing ──► done
   │            │           │            │
   └────────────┴───────────┴────────────┴──► failed
planning ──► running   (no confirm needed)
confirm  ──► planning  (plan changes requested)
finishing ──► running  (rework reopens a run)
done ──► running       (rework of a done run)
```

Job stage:

```
queued ──► running ──► merging ──► done        (code job)
queued ──► running ──► delivering ──► done     (files job)
done ──► running                               (rework)
queued | running | merging | delivering ──► dropped   (job drop: a human gave the job up)
dropped ──► running                            (rework)
merging ──► running                            (merge conflict sent back to the producer)
```

Group phase (inside a running job, one group at a time, in `position` order):

```
pending ──► produce ──► gate ──► passed           all gates PASS (or no gates)
                 ▲         │
                 └─────────┘ any CHANGES, round < max_round: round + 1
                           └──► capped             any CHANGES at max_round: move on, must-fix items become open points
```

A job whose last group is `passed` or `capped` moves to `merging` (code) or `delivering` (files).

Two moves go back:

- Merge conflict (`job stage <ref> running`): the last group's phase becomes `produce`, its round stays, the job is marked `conflict`.
- Rework (`job rework`): the chosen group's phase becomes `produce`, its round goes up by one and its `max_round` grows by one when needed, every later group goes back to `pending` with round 0, and for code jobs `base_sha` is reset to the feature branch head (the job branch is cut again from the feature branch).

Issue status:

```
open ──► ready ──► in_progress ──► in_review ──► done
  any ──► closed (will not do)      closed ──► open (reopen)
```

The core moves an issue to `in_progress` when a run starts from it, to `in_review` when the run's first PR is stored, and to `done` when every PR of its last run is merged (or the run is `done` with `no_pr` and a human marks it). Humans may set any status.

Question status: `open` (waits for a human) ──► `clarify` (waits for the agent's reply, then back to `open`) ──► `answered` ──► `resolved`.

Improvement status: `open` ──► `accepted` | `rejected`; `accepted` ──► `done` (only when its PR is merged, or by a human); any ──► `open` (back to review, clears the PR).
