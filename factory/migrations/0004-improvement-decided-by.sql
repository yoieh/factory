ALTER TABLE improvements ADD COLUMN decided_by TEXT;

CREATE INDEX IF NOT EXISTS events_by_kind ON events (kind, id);
