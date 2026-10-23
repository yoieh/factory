CREATE TABLE jobs_next (
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

INSERT INTO jobs_next (run_id, id, position, kind, repo, title, depends_on, reads, template, stage, group_pos, branch, worktree, conflict)
  SELECT run_id, id, position, kind, repo, title, depends_on, reads, template, stage, group_pos, branch, worktree, conflict FROM jobs;

DROP TABLE jobs;

ALTER TABLE jobs_next RENAME TO jobs;
