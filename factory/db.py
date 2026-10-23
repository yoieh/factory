import contextlib
import json
import os
import sqlite3

from . import FactoryError
from .util import now

MIGRATIONS = ["schema.sql", "migrations/0002-dropped-stage.sql", "migrations/0003-connected-agents.sql",
              "migrations/0004-improvement-decided-by.sql", "migrations/0005-run-started-by.sql",
              "migrations/0006-agent-roles.sql"]


def migration_sql(version):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), MIGRATIONS[version - 1])
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def latest_version():
    return len(MIGRATIONS)


def statements(sql):
    collected = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                collected.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        raise FactoryError("schema: incomplete SQL statement at the end")
    return collected


def stored_version(conn):
    table = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'meta'").fetchone()
    if not table:
        return 0
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    return int(row[0]) if row else 0


def migrate(conn, target=None):
    target = latest_version() if target is None else target
    if stored_version(conn) > latest_version():
        raise FactoryError(
            f"the database has schema {stored_version(conn)}, newer than this core knows ({latest_version()}); upgrade the core"
        )
    if stored_version(conn) >= target:
        return False
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for version in range(stored_version(conn) + 1, target + 1):
                for statement in statements(migration_sql(version)):
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
                    "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                    (str(version),),
                )
            broken = conn.execute("PRAGMA foreign_key_check").fetchall()
            if broken:
                raise FactoryError(f"migration left {len(broken)} broken references; nothing was changed")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    return True


def connect(path, target=None):
    conn = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn, target)
    return conn


@contextlib.contextmanager
def tx(conn):
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def event(conn, kind, run_id=None, job_id=None, **payload):
    conn.execute(
        "INSERT INTO events (run_id, job_id, at, kind, payload) VALUES (?, ?, ?, ?, ?)",
        (run_id, job_id, now(), kind, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
    )


def rows(cursor):
    return [dict(row) for row in cursor.fetchall()]


def one(cursor):
    row = cursor.fetchone()
    return dict(row) if row else None
