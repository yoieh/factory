import os
import sqlite3
import unittest
from unittest import mock

from helpers import FactoryCase

from factory import FactoryError, db

SAMPLES = {
    "projects": ("INSERT INTO projects (name, key, config_path, created_at) VALUES ('shop', 'SHOP', '/p', 'now')", "name"),
    "issues": ("INSERT INTO issues (key, project, title, body_path, created_at, updated_at) "
               "VALUES ('SHOP-1', 'shop', 't', '/b', 'now', 'now')", "key"),
    "runs": ("INSERT INTO runs (id, project, feature, slug, feature_branch, created_at) "
             "VALUES ('r1', 'shop', 'f', 's', 'feature/s', 'now')", "id"),
    "events": ("INSERT INTO events (run_id, at, kind) VALUES ('r1', 'now', 'run.new')", "kind"),
    "jobs": ("INSERT INTO jobs (run_id, id, position, kind, repo, title, stage) VALUES ('r1', 'j1', 1, 'code', 'api', 't', 'done')", "id"),
    "job_groups": ("INSERT INTO job_groups (run_id, job_id, position, step) VALUES ('r1', 'j1', 1, 'build')", "step"),
    "verdicts": ("INSERT INTO verdicts (run_id, job_id, group_pos, round, gate, verdict, path) "
                 "VALUES ('r1', 'j1', 1, 1, 'review-code', 'PASS', '/p')", "verdict"),
}


class MigrationTest(FactoryCase):
    def path(self):
        return os.path.join(self.tmp, "factory.db")

    def test_each_older_version_migrates_without_data_loss(self):
        for version in range(db.latest_version()):
            with self.subTest(version=version):
                if os.path.exists(self.path()):
                    os.unlink(self.path())
                conn = db.connect(self.path(), target=version)
                self.assertEqual(db.stored_version(conn), version)
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
                written = {}
                for table, (sql, column) in SAMPLES.items():
                    if table in tables:
                        conn.execute(sql)
                        written[table] = [row[0] for row in conn.execute(f"SELECT {column} FROM {table}")]
                conn.close()
                conn = db.connect(self.path())
                self.assertEqual(db.stored_version(conn), db.latest_version())
                for table, values in written.items():
                    column = SAMPLES[table][1]
                    self.assertEqual([row[0] for row in conn.execute(f"SELECT {column} FROM {table}")], values)
                conn.close()

    def test_version_2_allows_dropped_jobs_and_keeps_foreign_keys(self):
        conn = db.connect(self.path())
        conn.execute(SAMPLES["projects"][0])
        conn.execute(SAMPLES["runs"][0])
        conn.execute(SAMPLES["jobs"][0])
        conn.execute(SAMPLES["job_groups"][0])
        conn.execute("UPDATE jobs SET stage = 'dropped'")
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("UPDATE jobs SET stage = 'paused'")
        conn.execute("DELETE FROM runs")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM job_groups").fetchone()[0], 0)
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        conn.close()

    def test_connect_settings(self):
        conn = db.connect(self.path())
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
        self.assertFalse(db.migrate(conn))
        conn.close()

    def test_a_newer_database_is_refused(self):
        conn = db.connect(self.path())
        conn.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
        conn.close()
        with self.assertRaises(FactoryError) as caught:
            db.connect(self.path())
        self.assertIn("newer than this core", caught.exception.message)

    def test_a_failed_migration_rolls_back(self):
        conn = db.connect(self.path())
        conn.close()
        latest = db.latest_version()
        real = db.migration_sql

        def with_a_broken_step(version):
            return "CREATE TABLE extra (id INTEGER);\nTHIS IS NOT SQL;\n" if version > latest else real(version)

        with mock.patch.object(db, "MIGRATIONS", db.MIGRATIONS + ["broken.sql"]), \
                mock.patch.object(db, "migration_sql", with_a_broken_step):
            with self.assertRaises(sqlite3.OperationalError):
                db.connect(self.path())
        conn = sqlite3.connect(self.path())
        self.assertEqual(conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0], str(latest))
        self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name = 'extra'").fetchone())
        conn.close()

    def test_statement_splitter(self):
        parts = db.statements("CREATE TABLE a (x TEXT DEFAULT ';');\n\nCREATE INDEX i ON a (x);\n")
        self.assertEqual(len(parts), 2)
        with self.assertRaises(FactoryError):
            db.statements("CREATE TABLE a (x TEXT")


if __name__ == "__main__":
    unittest.main()
