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
  created_at     TEXT NOT NULL
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
             CHECK (stage IN ('queued', 'running', 'merging', 'delivering', 'done')),
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
  UNIQUE (run_id, key)
);

CREATE INDEX IF NOT EXISTS issues_by_project ON issues (project, status);
CREATE INDEX IF NOT EXISTS questions_by_run ON questions (run_id, status);
CREATE INDEX IF NOT EXISTS question_messages_by_question ON question_messages (question_id, id);
CREATE INDEX IF NOT EXISTS events_by_run ON events (run_id, id);
CREATE INDEX IF NOT EXISTS sessions_by_id ON sessions (session_id, added_at);
CREATE INDEX IF NOT EXISTS runs_by_project ON runs (project, status);
