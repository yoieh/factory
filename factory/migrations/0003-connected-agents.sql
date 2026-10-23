CREATE TABLE IF NOT EXISTS connected_agents (
  name        TEXT PRIMARY KEY,
  description TEXT,
  added_at    TEXT NOT NULL,
  seen_at     TEXT
);

INSERT OR IGNORE INTO connected_agents (name, added_at, seen_at)
  SELECT author, MIN(at), MAX(at) FROM question_messages
  WHERE kind IN ('answer', 'clarify') AND author NOT IN ('human', 'dashboard') AND length(author) <= 40
  GROUP BY author;

CREATE INDEX IF NOT EXISTS question_messages_by_author ON question_messages (author, id);
